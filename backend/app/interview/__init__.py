"""Interview domain (mirrors Go internal/interview)."""
from .errors import (
    AnswerAlreadyCommitted,
    DomainError,
    ErrorCode,
    IdempotencyConflict,
    PersistenceConflict,
    PersistenceNotFound,
    StoreConflict,
    StoreNotFound,
    conflict,
    internal,
    unavailable,
    validation,
)
from .memory_store import MemoryStore
from .profile_builder import ProfileBuilder
from .recovery import SessionEventPage, SessionSnapshot, ReviewSnapshot
from .service import Service

__all__ = [
    "AnswerAlreadyCommitted", "DomainError", "ErrorCode", "IdempotencyConflict",
    "PersistenceConflict", "PersistenceNotFound", "StoreConflict", "StoreNotFound",
    "conflict", "internal", "unavailable", "validation",
    "MemoryStore", "ProfileBuilder", "SessionEventPage", "SessionSnapshot", "ReviewSnapshot",
    "Service",
]
