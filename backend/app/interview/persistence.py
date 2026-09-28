"""Durable execution contracts mirroring Go interview/persistence.go (pragmatic subset)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .types import InterviewSession


class CommandStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class CommandSpec:
    id: str
    principal_id: str = ""
    session_id: str = ""
    action: str = ""
    idempotency_key: str = ""
    subject_id: str = ""
    request_hash: str = ""


@dataclass
class Command:
    id: str
    principal_id: str = ""
    session_id: str = ""
    action: str = ""
    idempotency_key: str = ""
    subject_id: str = ""
    request_hash: str = ""
    status: CommandStatus = CommandStatus.PENDING
    result: str | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class CommandTransition:
    status: CommandStatus
    result: str | None = None
    error: str | None = None


@dataclass
class SessionEventSpec:
    event_id: str = ""
    session_id: str = ""
    command_id: str = ""
    type: str = ""
    payload: str = ""


@dataclass
class SessionEvent:
    event_id: str
    session_id: str
    sequence: int
    command_id: str = ""
    type: str = ""
    payload: str = ""
    created_at: datetime | None = None


@dataclass
class AnswerCommitSpec:
    session: InterviewSession
    expected_version: int
    command_id: str
    result: str
    event: SessionEventSpec = field(default_factory=SessionEventSpec)


VALID_COMMAND_STATUSES = {CommandStatus.PENDING, CommandStatus.RUNNING, CommandStatus.SUCCEEDED, CommandStatus.FAILED}
