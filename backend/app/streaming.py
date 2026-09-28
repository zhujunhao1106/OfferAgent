"""Streaming writers: SSE (chat) and NDJSON (interview/stream), per design doc §2.5."""
from __future__ import annotations

import json

SSE_HEADERS = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}

NDJSON_HEADERS = {
    "Content-Type": "application/x-ndjson; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
}


def sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def sse_done() -> str:
    return "data: [DONE]\n\n"


def ndjson_line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"
