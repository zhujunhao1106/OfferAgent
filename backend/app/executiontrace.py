"""Sanitized, request-scoped progress events mirroring Go executiontrace pkg.

Never receives prompts, source material, model output, or raw errors.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class Status(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Event:
    id: str
    stage: str
    label: str
    status: Status
    agent: str = ""
    detail: str = ""
    at: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict:
        result = {"id": self.id, "stage": self.stage, "label": self.label, "status": self.status.value}
        if self.detail:
            result["detail"] = self.detail
        if self.agent:
            result["agent"] = self.agent
        result["at"] = self.at
        if self.duration_ms:
            result["durationMs"] = self.duration_ms
        return result


Sink = callable

_sink_var: ContextVar[Sink | None] = ContextVar("executiontrace_sink", default=None)


def with_sink(sink: Sink):
    """Attach one request-local sink (deliberately not composed with a parent)."""
    return _sink_var.set(sink)


def reset_sink(token) -> None:
    _sink_var.reset(token)


def emit(event: Event) -> None:
    sink = _sink_var.get()
    if sink is None:
        return
    try:
        sink(event)
    except Exception:
        pass


def new_id() -> str:
    return secrets.token_hex(12)  # 24 hex chars


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Span:
    def __init__(self, event: Event, started_ts: float):
        self._event = event
        self._started_ts = started_ts
        self._done = False

    def end(self, err: Exception | None, success_detail: str) -> None:
        if self._done:
            return
        self._done = True
        self._event.at = now_iso()
        self._event.duration_ms = int((time.time() - self._started_ts) * 1000)
        if err is None:
            self._event.status = Status.COMPLETED
            self._event.detail = success_detail
        else:
            self._event.status = Status.FAILED
            self._event.detail = failure_class(err)
        emit(self._event)


def start(stage: str, label: str, agent: str = "") -> Span:
    event_id = new_id()
    emit(Event(id=event_id, stage=stage, label=label, status=Status.QUEUED, agent=agent, at=now_iso()))
    emit(Event(id=event_id, stage=stage, label=label, status=Status.RUNNING, agent=agent, at=now_iso()))
    return Span(Event(id=event_id, stage=stage, label=label, status=Status.RUNNING, agent=agent), time.time())


def failure_class(err: Exception | None) -> str:
    if err is None:
        return "unavailable"
    if isinstance(err, asyncio.CancelledError):
        return "canceled"
    if isinstance(err, TimeoutError):
        return "timeout"
    return "unavailable"
