"""Interview domain errors mirroring Go interview/errors.go + persistence sentinels."""
from __future__ import annotations


class ErrorCode:
    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INVALID_STATE = "invalid_state"
    GROUNDING = "grounding"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INTERNAL = "internal"


class DomainError(Exception):
    def __init__(self, code: str, message: str, field: str = "", cause: Exception | None = None):
        self.code = code
        self.message = message
        self.field = field
        self.cause = cause
        if field:
            super().__init__(f"{code}: {message} ({field})")
        else:
            super().__init__(f"{code}: {message}")


def validation(field: str, message: str) -> DomainError:
    return DomainError(ErrorCode.VALIDATION, message, field=field)


def conflict(message: str, cause: Exception | None = None) -> DomainError:
    return DomainError(ErrorCode.CONFLICT, message, cause=cause)


def unavailable(message: str, cause: Exception | None = None) -> DomainError:
    return DomainError(ErrorCode.SERVICE_UNAVAILABLE, message, cause=cause)


def internal(message: str, cause: Exception | None = None) -> DomainError:
    return DomainError(ErrorCode.INTERNAL, message, cause=cause)


def is_code(err: Exception, code: str) -> bool:
    return isinstance(err, DomainError) and err.code == code


class StoreError(Exception):
    pass


class StoreNotFound(StoreError):
    pass


class StoreConflict(StoreError):
    pass


class PersistenceNotFound(StoreError):
    pass


class PersistenceConflict(StoreError):
    pass


class IdempotencyConflict(StoreError):
    pass


class AnswerAlreadyCommitted(StoreError):
    pass
