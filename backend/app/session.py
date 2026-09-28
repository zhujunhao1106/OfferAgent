"""In-memory session registry (mirrors Go session.Store)."""
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


class Store:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}

    def create(self, id: str | None = None) -> Session:
        sid = id or secrets.token_hex(16)  # 32 hex chars
        now = time.time()
        with self._lock:
            if sid in self._sessions:
                raise ValueError(f"duplicate session id: {sid}")
            session = Session(id=sid, created_at=now, updated_at=now)
            self._sessions[sid] = session
        return session

    def get(self, id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(id)
