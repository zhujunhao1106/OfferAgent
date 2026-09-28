"""Error model: API-style vs legacy envelopes (mirrors Go writeAPIError/writeLegacyError)."""
from __future__ import annotations

from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Structured API error -> {"error": {code, message, retryable, field?}}."""

    def __init__(self, status: int, code: str, message: str, retryable: bool = False, field: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.field = field

    def payload(self) -> dict:
        body = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.field:
            body["field"] = self.field
        return {"error": body}


def json_response(content, status: int = 200) -> JSONResponse:
    """JSON response matching Go writeJSON: charset + Cache-Control: no-store."""
    return JSONResponse(
        content,
        status_code=status,
        media_type="application/json; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def legacy_error(message: str) -> dict:
    return {"error": message}


def transcribe_error(message: str, retryable: bool) -> dict:
    return {"error": message, "retryable": retryable}
