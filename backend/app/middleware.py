"""Security middleware + auth dependency (mirrors Go withMiddleware + requireAuth)."""
from __future__ import annotations

import hmac
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .errors import ApiError

UNAUTHORIZED = "Unauthorized"
UNAUTHORIZED_NOT_CONFIGURED = "Server authentication is not configured"


def new_request_id() -> str:
    return secrets.token_hex(12)  # 24 hex chars


class Security:
    def __init__(self, api_key: str, require_auth: bool, allowed_origins: list[str]):
        self.api_key = api_key
        self.require_auth = require_auth
        self.allowed_origins = allowed_origins

    async def middleware(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or new_request_id()
        headers = {
            "X-Request-ID": request_id,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        }
        origin = request.headers.get("origin")
        if origin:
            if origin not in self.allowed_origins:
                return JSONResponse(
                    {"error": {"code": "origin_not_allowed", "message": "Origin is not allowed", "retryable": False}},
                    status_code=403,
                    media_type="application/json; charset=utf-8",
                    headers={**headers, "Cache-Control": "no-store"},
                )
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
        headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-File-Name, X-Request-ID"
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=headers)
        response = await call_next(request)
        for key, value in headers.items():
            response.headers.setdefault(key, value)
        return response

    async def authenticate(self, request: Request):
        if not self.api_key:
            if not self.require_auth:
                return
            raise ApiError(401, "unauthorized", UNAUTHORIZED_NOT_CONFIGURED, False)
        auth = request.headers.get("authorization", "")
        if not (len(auth) > 7 and auth[:7].lower() == "bearer "):
            raise ApiError(401, "unauthorized", UNAUTHORIZED, False)
        if not hmac.compare_digest(auth[7:], self.api_key):
            raise ApiError(401, "unauthorized", UNAUTHORIZED, False)
