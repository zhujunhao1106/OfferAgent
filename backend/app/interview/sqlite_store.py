"""SQLite store mirroring Go interview/sqlite_store.go + sqlite_persistence.go (pragmatic 3 tables)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import aiosqlite

from .errors import (
    AnswerAlreadyCommitted,
    IdempotencyConflict,
    PersistenceConflict,
    PersistenceNotFound,
    StoreConflict,
    StoreNotFound,
)
from .persistence import (
    VALID_COMMAND_STATUSES,
    AnswerCommitSpec,
    Command,
    CommandSpec,
    CommandStatus,
    CommandTransition,
    SessionEvent,
    SessionEventSpec,
)
from .types import InterviewSession


def _now_ms() -> int:
    return int(time.time() * 1000)


def _sqlite_time(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)


def _bounded_limit(limit: int, fallback: int, maximum: int) -> int:
    if limit <= 0:
        return fallback
    if limit > maximum:
        return maximum
    return limit


def _marshal_session(session: InterviewSession) -> str:
    return json.dumps(
        {
            "session": session.model_dump(mode="json"),
            "sourceContents": {id: doc.content for id, doc in session.sources.documents.items()},
        },
        ensure_ascii=False,
    )


def _unmarshal_session(payload: str) -> InterviewSession:
    data = json.loads(payload)
    session = InterviewSession.model_validate(data["session"])
    for id, content in data.get("sourceContents", {}).items():
        document = session.sources.documents.get(id)
        if document is not None:
            document.content = content
    return session


_COMMAND_COLUMNS = (
    "id, principal_id, session_id, action, idempotency_key, subject_id, request_hash, "
    "status, result_json, error_json, created_at_ms, updated_at_ms"
)
_EVENT_COLUMNS = "event_id, session_id, sequence, command_id, event_type, payload_json, created_at_ms"


class SQLiteStore:
    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    @classmethod
    async def open(cls, path: str) -> "SQLiteStore":
        db = await aiosqlite.connect(path)
        for pragma in (
            "PRAGMA busy_timeout = 5000",
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            "PRAGMA foreign_keys = ON",
        ):
            await db.execute(pragma)
        await db.commit()
        store = cls(db)
        await store._migrate()
        return store

    async def close(self) -> None:
        await self._db.close()

    async def _migrate(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS interview_sessions (
                id TEXT PRIMARY KEY,
                version INTEGER NOT NULL CHECK (version >= 0),
                snapshot_json BLOB NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS interview_commands (
                id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL,
                action TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                subject_id TEXT NOT NULL DEFAULT '',
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
                result_json BLOB,
                error_json BLOB,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                UNIQUE (principal_id, session_id, action, idempotency_key)
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_cmd_subject
                ON interview_commands (principal_id, session_id, action, subject_id)
                WHERE subject_id <> ''
            """,
            """
            CREATE TABLE IF NOT EXISTS interview_session_events (
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK (sequence > 0),
                event_id TEXT NOT NULL UNIQUE,
                command_id TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL,
                payload_json BLOB NOT NULL,
                created_at_ms INTEGER NOT NULL,
                PRIMARY KEY (session_id, sequence)
            )
            """,
        ]
        for statement in statements:
            await self._db.execute(statement)
        await self._db.commit()

    # --- aggregate store ---

    async def create(self, session: InterviewSession) -> None:
        payload = _marshal_session(session)
        now = _now_ms()
        cursor = await self._db.execute(
            "INSERT INTO interview_sessions (id, version, snapshot_json, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
            (session.id, session.version, payload, now, now),
        )
        await self._db.commit()
        if cursor.rowcount != 1:
            raise StoreConflict()

    async def load(self, id: str) -> InterviewSession:
        cursor = await self._db.execute(
            "SELECT version, snapshot_json FROM interview_sessions WHERE id = ?", (id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise StoreNotFound()
        version, payload = row
        session = _unmarshal_session(payload)
        if session.id != id or session.version != version:
            raise ValueError(f"interview sqlite store: corrupt snapshot {id!r}")
        return session

    async def save(self, session: InterviewSession, expected_version: int) -> None:
        if session.version != expected_version + 1:
            await self._classify_save_miss(session.id)
            return
        payload = _marshal_session(session)
        cursor = await self._db.execute(
            "UPDATE interview_sessions SET version = ?, snapshot_json = ?, updated_at_ms = ? "
            "WHERE id = ? AND version = ?",
            (session.version, payload, _now_ms(), session.id, expected_version),
        )
        await self._db.commit()
        if cursor.rowcount == 1:
            return
        await self._classify_save_miss(session.id)

    async def _classify_save_miss(self, id: str) -> None:
        cursor = await self._db.execute("SELECT 1 FROM interview_sessions WHERE id = ?", (id,))
        row = await cursor.fetchone()
        if row is None:
            raise StoreNotFound()
        raise StoreConflict()

    # --- command persistence ---

    async def create_or_get_command(self, spec: CommandSpec) -> tuple[Command, bool]:
        _validate_command_spec(spec)
        now = _now_ms()
        cursor = await self._db.execute(
            "INSERT INTO interview_commands (id, principal_id, session_id, action, idempotency_key, "
            "subject_id, request_hash, status, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (spec.id, spec.principal_id, spec.session_id, spec.action, spec.idempotency_key,
             spec.subject_id, spec.request_hash, CommandStatus.PENDING.value, now, now),
        )
        await self._db.commit()
        if cursor.rowcount == 1:
            command = await self.get_command(spec.id)
            return command, True

        try:
            command = await self._get_command_by_idempotency_key(
                spec.principal_id, spec.session_id, spec.action, spec.idempotency_key
            )
        except PersistenceNotFound:
            if spec.subject_id:
                try:
                    command = await self._get_command_by_subject(
                        spec.principal_id, spec.session_id, spec.action, spec.subject_id
                    )
                except PersistenceNotFound:
                    raise PersistenceConflict(f"command id {spec.id!r}")
                raise AnswerAlreadyCommitted(f"session {spec.session_id!r} subject {spec.subject_id!r}")
            raise PersistenceConflict(f"command id {spec.id!r}")
        if _same_command_spec(command, spec):
            return command, False
        raise IdempotencyConflict(f"session {spec.session_id!r} key {spec.idempotency_key!r}")

    async def get_command(self, id: str) -> Command:
        cursor = await self._db.execute(
            f"SELECT {_COMMAND_COLUMNS} FROM interview_commands WHERE id = ?", (id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise PersistenceNotFound()
        return _scan_command(row)

    async def _get_command_by_idempotency_key(
        self, principal_id: str, session_id: str, action: str, key: str
    ) -> Command:
        cursor = await self._db.execute(
            f"SELECT {_COMMAND_COLUMNS} FROM interview_commands "
            "WHERE principal_id = ? AND session_id = ? AND action = ? AND idempotency_key = ?",
            (principal_id, session_id, action, key),
        )
        row = await cursor.fetchone()
        if row is None:
            raise PersistenceNotFound()
        return _scan_command(row)

    async def _get_command_by_subject(
        self, principal_id: str, session_id: str, action: str, subject_id: str
    ) -> Command:
        cursor = await self._db.execute(
            f"SELECT {_COMMAND_COLUMNS} FROM interview_commands "
            "WHERE principal_id = ? AND session_id = ? AND action = ? AND subject_id = ?",
            (principal_id, session_id, action, subject_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise PersistenceNotFound()
        return _scan_command(row)

    async def transition_command(
        self, id: str, expected: CommandStatus, transition: CommandTransition
    ) -> Command:
        if not id:
            raise ValueError("interview persistence: command id is empty")
        if expected not in VALID_COMMAND_STATUSES or transition.status not in VALID_COMMAND_STATUSES:
            raise ValueError("interview persistence: invalid command status")
        cursor = await self._db.execute(
            "UPDATE interview_commands SET status = ?, result_json = ?, error_json = ?, updated_at_ms = ? "
            "WHERE id = ? AND status = ?",
            (transition.status.value, transition.result, transition.error, _now_ms(), id, expected.value),
        )
        await self._db.commit()
        if cursor.rowcount != 1:
            try:
                await self.get_command(id)
            except PersistenceNotFound:
                raise
            raise PersistenceConflict(f"command {id!r} status changed")
        return await self.get_command(id)

    # --- events ---

    async def _next_sequence(self, session_id: str) -> int:
        cursor = await self._db.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM interview_session_events WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return row[0]

    async def append_session_event(self, spec: SessionEventSpec) -> tuple[SessionEvent, bool]:
        if not spec.event_id:
            raise ValueError("interview persistence: event id is empty")
        if not spec.session_id:
            raise ValueError("interview persistence: event session id is empty")
        if not spec.type:
            raise ValueError("interview persistence: event type is empty")
        try:
            existing = await self._get_session_event_by_id(spec.event_id)
        except PersistenceNotFound:
            existing = None
        if existing is not None:
            if _same_event_spec(existing, spec):
                return existing, False
            raise PersistenceConflict(f"event id {spec.event_id!r}")

        try:
            sequence = await self._next_sequence(spec.session_id)
            now = _now_ms()
            await self._db.execute(
                "INSERT INTO interview_session_events "
                "(session_id, sequence, event_id, command_id, event_type, payload_json, created_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (spec.session_id, sequence, spec.event_id, spec.command_id, spec.type, spec.payload, now),
            )
            await self._db.commit()
        except aiosqlite.IntegrityError:
            await self._db.rollback()
            existing = await self._get_session_event_by_id(spec.event_id)
            if _same_event_spec(existing, spec):
                return existing, False
            raise PersistenceConflict(f"event id {spec.event_id!r}")
        return SessionEvent(
            event_id=spec.event_id, session_id=spec.session_id, sequence=sequence,
            command_id=spec.command_id, type=spec.type, payload=spec.payload,
            created_at=_sqlite_time(now),
        ), True

    async def _get_session_event_by_id(self, event_id: str) -> SessionEvent:
        cursor = await self._db.execute(
            f"SELECT {_EVENT_COLUMNS} FROM interview_session_events WHERE event_id = ?", (event_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise PersistenceNotFound()
        return _scan_event(row)

    async def list_session_events(self, session_id: str, after_sequence: int, limit: int) -> list[SessionEvent]:
        if not session_id:
            raise ValueError("interview persistence: event session id is empty")
        limit = _bounded_limit(limit, 100, 1000)
        cursor = await self._db.execute(
            f"SELECT {_EVENT_COLUMNS} FROM interview_session_events "
            "WHERE session_id = ? AND sequence > ? ORDER BY sequence ASC LIMIT ?",
            (session_id, after_sequence, limit),
        )
        rows = await cursor.fetchall()
        return [_scan_event(row) for row in rows]

    # --- atomic answer commit ---

    async def commit_answer(self, spec: AnswerCommitSpec) -> None:
        if not spec.command_id:
            raise ValueError("interview persistence: answer command id is empty")
        if not spec.session.id or spec.event.session_id != spec.session.id or spec.event.command_id != spec.command_id:
            raise ValueError("interview persistence: answer commit scope is invalid")
        if spec.session.version != spec.expected_version + 1:
            raise StoreConflict()
        if not spec.event.event_id or not spec.event.type:
            raise ValueError("interview persistence: answer committed event is invalid")

        snapshot = _marshal_session(spec.session)
        now = _now_ms()
        try:
            cursor = await self._db.execute(
                "UPDATE interview_sessions SET version = ?, snapshot_json = ?, updated_at_ms = ? "
                "WHERE id = ? AND version = ?",
                (spec.session.version, snapshot, now, spec.session.id, spec.expected_version),
            )
            if cursor.rowcount != 1:
                check = await self._db.execute(
                    "SELECT 1 FROM interview_sessions WHERE id = ?", (spec.session.id,)
                )
                row = await check.fetchone()
                if row is None:
                    raise StoreNotFound()
                raise StoreConflict()

            cursor = await self._db.execute(
                "UPDATE interview_commands SET status = ?, result_json = ?, error_json = NULL, updated_at_ms = ? "
                "WHERE id = ? AND session_id = ? AND action = ? AND status = ?",
                (CommandStatus.SUCCEEDED.value, spec.result, now,
                 spec.command_id, spec.session.id, "answer", CommandStatus.RUNNING.value),
            )
            if cursor.rowcount != 1:
                check = await self._db.execute(
                    "SELECT status FROM interview_commands WHERE id = ?", (spec.command_id,)
                )
                row = await check.fetchone()
                if row is None:
                    raise PersistenceNotFound()
                raise PersistenceConflict(f"answer command {spec.command_id!r} status is {row[0]!r}")

            sequence = await self._next_sequence(spec.session.id)
            await self._db.execute(
                "INSERT INTO interview_session_events "
                "(session_id, sequence, event_id, command_id, event_type, payload_json, created_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (spec.session.id, sequence, spec.event.event_id, spec.command_id, spec.event.type, spec.event.payload, now),
            )
            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise


def _scan_command(row) -> Command:
    (id, principal_id, session_id, action, idempotency_key, subject_id, request_hash,
     status, result_json, error_json, created_ms, updated_ms) = row
    return Command(
        id=id, principal_id=principal_id or "", session_id=session_id, action=action,
        idempotency_key=idempotency_key, subject_id=subject_id or "", request_hash=request_hash,
        status=CommandStatus(status), result=result_json, error=error_json,
        created_at=_sqlite_time(created_ms), updated_at=_sqlite_time(updated_ms),
    )


def _scan_event(row) -> SessionEvent:
    (event_id, session_id, sequence, command_id, event_type, payload_json, created_ms) = row
    return SessionEvent(
        event_id=event_id, session_id=session_id, sequence=sequence,
        command_id=command_id or "", type=event_type, payload=payload_json,
        created_at=_sqlite_time(created_ms),
    )


def _validate_command_spec(spec: CommandSpec) -> None:
    if not spec.id:
        raise ValueError("interview persistence: command id is empty")
    if not spec.session_id:
        raise ValueError("interview persistence: command session id is empty")
    if not spec.action:
        raise ValueError("interview persistence: command action is empty")
    if not spec.idempotency_key:
        raise ValueError("interview persistence: command idempotency key is empty")
    if not spec.request_hash:
        raise ValueError("interview persistence: command request hash is empty")


def _same_command_spec(command: Command, spec: CommandSpec) -> bool:
    return (
        command.principal_id == spec.principal_id
        and command.session_id == spec.session_id
        and command.action == spec.action
        and command.idempotency_key == spec.idempotency_key
        and command.subject_id == spec.subject_id
        and command.request_hash == spec.request_hash
    )


def _same_event_spec(event: SessionEvent, spec: SessionEventSpec) -> bool:
    return (
        event.event_id == spec.event_id
        and event.session_id == spec.session_id
        and event.command_id == spec.command_id
        and event.type == spec.type
        and event.payload == spec.payload
    )
