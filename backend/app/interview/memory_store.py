"""In-memory session store + clock/id generators mirroring Go interview/memory_store.go."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from .errors import StoreConflict, StoreNotFound
from .types import InterviewSession


def clone_session(session: InterviewSession) -> InterviewSession:
    return session.model_copy(deep=True)


class MemoryStore:
    def __init__(self):
        self._sessions: dict[str, InterviewSession] = {}

    async def create(self, session: InterviewSession) -> None:
        if session.id in self._sessions:
            raise StoreConflict()
        self._sessions[session.id] = clone_session(session)

    async def load(self, id: str) -> InterviewSession:
        session = self._sessions.get(id)
        if session is None:
            raise StoreNotFound()
        return clone_session(session)

    async def save(self, session: InterviewSession, expected_version: int) -> None:
        current = self._sessions.get(session.id)
        if current is None:
            raise StoreNotFound()
        if current.version != expected_version or session.version != expected_version + 1:
            raise StoreConflict()
        self._sessions[session.id] = clone_session(session)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class RandomIDs:
    def new_id(self, prefix: str) -> str:
        return prefix + "-" + secrets.token_hex(12)
