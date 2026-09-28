"""In-memory session registry + per-session conversation memory (mirrors Go session pkg)."""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    id: str
    created_at: float
    updated_at: float
    values: dict = field(default_factory=dict)


@dataclass
class Message:
    role: str
    content: str
    at: float = 0.0
    metadata: dict = field(default_factory=dict)


def _new_id() -> str:
    return secrets.token_hex(16)  # 32 hex chars


def _clone_metadata(metadata: dict | None) -> dict | None:
    return None if metadata is None else dict(metadata)


def _clone_session(session: Session) -> Session:
    return Session(
        id=session.id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        values=dict(session.values),
    )


def _clone_messages(messages: list[Message]) -> list[Message]:
    return [Message(role=m.role, content=m.content, at=m.at, metadata=dict(m.metadata)) for m in messages]


class Store:
    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}

    def create(self, *ids: str) -> Session:
        id = ids[0].strip() if ids else ""
        if not id:
            id = _new_id()
        now = time.time()
        created = Session(id=id, created_at=now, updated_at=now, values={})
        with self._lock:
            if id in self._sessions:
                raise ValueError("session: id already exists")
            self._sessions[id] = created
        return _clone_session(created)

    def get(self, id: str) -> Session | None:
        with self._lock:
            stored = self._sessions.get(id)
            return _clone_session(stored) if stored is not None else None

    def set_value(self, id: str, key: str, value: str) -> None:
        if not key.strip():
            raise ValueError("session: value key is required")
        with self._lock:
            stored = self._sessions.get(id)
            if stored is None:
                raise ValueError("session: id does not exist")
            if not stored.values:
                stored.values = {}
            stored.values[key] = value
            stored.updated_at = time.time()
            self._sessions[id] = stored

    def delete(self, id: str) -> bool:
        with self._lock:
            if id not in self._sessions:
                return False
            del self._sessions[id]
            return True

    def ids(self) -> list[str]:
        with self._lock:
            return sorted(self._sessions.keys())


class Memory:
    def __init__(self, max_messages: int):
        self._lock = threading.RLock()
        self._by_session: dict[str, list[Message]] = {}
        self._max_messages = max_messages

    def append(self, session_id: str, message: Message) -> None:
        session_id = session_id.strip()
        if not session_id:
            raise ValueError("session: session id is required")
        if message.at == 0:
            message.at = time.time()
        message.metadata = _clone_metadata(message.metadata) or {}
        with self._lock:
            history = list(self._by_session.get(session_id, []))
            history.append(message)
            if self._max_messages > 0 and len(history) > self._max_messages:
                history = history[-self._max_messages:]
            self._by_session[session_id] = history

    def add(self, session_id: str, role: str, content: str) -> None:
        self.append(session_id, Message(role=role, content=content))

    def messages(self, session_id: str) -> list[Message]:
        with self._lock:
            return _clone_messages(self._by_session.get(session_id, []))

    def replace(self, session_id: str, messages: list[Message]) -> None:
        session_id = session_id.strip()
        if not session_id:
            raise ValueError("session: session id is required")
        copy_messages = _clone_messages(messages)
        if self._max_messages > 0 and len(copy_messages) > self._max_messages:
            copy_messages = copy_messages[-self._max_messages:]
        with self._lock:
            self._by_session[session_id] = copy_messages

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._by_session.pop(session_id, None)

    def session_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._by_session.keys())
