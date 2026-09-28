#!/usr/bin/env python3
"""Record golden contract snapshots from the running Go API (P0 step 1).

Replayable by the Python backend's contract test: for each case, send the same
request and assert identical (status, response_headers, body).

Usage:
  GOLDEN_MODE=authed|unconfigured python record_golden.py [base_url]

  authed:        Go container runs with OPENAI_API_KEY=<placeholder> (non-empty,
                 so ModelConfigured=true), OFFERPILOT_REQUIRE_AUTH=true,
                 OFFERPILOT_API_KEY=golden-test-key,
                 OFFERPILOT_ALLOWED_ORIGINS=http://allowed.example
  unconfigured:  Go container runs with empty OPENAI_API_KEY (ModelConfigured=false).

Writes tests/golden/api/<name>.json.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3001"
OUT = Path(__file__).resolve().parent / "api"
MODE = os.environ.get("GOLDEN_MODE", "authed")
KEY = os.environ.get("GOLDEN_KEY", "golden-test-key")

# Response headers stable enough to record (exclude Date / X-Request-ID / Server).
STABLE_HEADERS = {
    "content-type", "cache-control",
    "access-control-allow-origin", "access-control-allow-methods",
    "access-control-allow-headers", "vary", "x-accel-buffering",
}


def auth():
    return {"Authorization": f"Bearer {KEY}"}


def j(d):
    return json.dumps(d, ensure_ascii=False).encode()


def cases():
    if MODE == "unconfigured":
        return [
            ("health_ready_unconfigured", "GET", "/health/ready", None, None),
            ("interview_unconfigured", "POST", "/api/interview", auth(), j({"action": "start", "config": {}, "materials": {}})),
            ("chat_unconfigured", "POST", "/api/chat", {**auth(), "Content-Type": "application/json"}, j({"message": "hi"})),
            ("match_unconfigured", "POST", "/api/match", {**auth(), "Content-Type": "application/json"}, j({"jd": "a", "resume": "b"})),
            ("resume_unconfigured", "POST", "/api/resume/diagnose", {**auth(), "Content-Type": "application/json"}, j({"content": "x"})),
            ("crawl_unconfigured", "POST", "/api/crawl", {**auth(), "Content-Type": "application/json"}, j({"url": "https://example.com"})),
        ]
    a = auth()
    ct = {"Content-Type": "application/json"}
    return [
        ("health_ok", "GET", "/health", None, None),
        ("health_live", "GET", "/health/live", None, None),
        ("health_ready", "GET", "/health/ready", None, None),
        ("session_no_auth", "POST", "/api/session", None, b""),
        ("session_with_auth", "POST", "/api/session", a, b""),
        ("session_bad_origin", "POST", "/api/session", {**a, "Origin": "https://evil.example"}, b""),
        ("options_preflight", "OPTIONS", "/api/session", {"Origin": "http://allowed.example", "Access-Control-Request-Method": "POST"}, None),
        ("chat_invalid_json", "POST", "/api/chat", {**a, **ct}, b"{not json"),
        ("chat_empty_message", "POST", "/api/chat", {**a, **ct}, j({"message": ""})),
        ("chat_message_too_long", "POST", "/api/chat", {**a, **ct}, j({"message": "x" * 20001})),
        ("chat_unknown_field", "POST", "/api/chat", {**a, **ct}, j({"message": "hi", "bogus": 1})),
        ("interview_invalid_action", "POST", "/api/interview", {**a, **ct}, j({"action": "bogus"})),
        ("interview_start_bad_count", "POST", "/api/interview", {**a, **ct}, j({"action": "start", "config": {"questionCount": 99}, "materials": {}})),
        ("interview_answer_not_found", "POST", "/api/interview", {**a, **ct}, j({"action": "answer", "interviewId": "nonexistent", "questionId": "q1", "clientAnswerId": "c1", "answer": {"text": "x", "inputMode": "text"}})),
        ("interview_report_not_found", "POST", "/api/interview", {**a, **ct}, j({"action": "report", "interviewId": "nonexistent"})),
        ("match_missing", "POST", "/api/match", {**a, **ct}, j({"jd": "", "resume": ""})),
        ("match_unknown_field", "POST", "/api/match", {**a, **ct}, j({"jd": "a", "resume": "b", "bogus": 1})),
        ("resume_empty", "POST", "/api/resume/diagnose", {**a, **ct}, j({"content": ""})),
        ("resume_too_many_images", "POST", "/api/resume/diagnose", {**a, **ct}, j({"content": "x", "images": ["data:image/png;base64,iVBORw0KGgo="] * 4})),
        ("crawl_empty_url", "POST", "/api/crawl", {**a, **ct}, j({"url": ""})),
        ("tts_empty_text", "POST", "/api/tts", {**a, **ct}, j({"text": ""})),
        ("transcribe_empty", "POST", "/api/transcribe", a, b""),
        ("snapshot_not_found", "GET", "/api/v1/interviews/nonexistent", a, None),
        ("events_invalid_limit", "GET", "/api/v1/interviews/nonexistent/events?limit=0", a, None),
        ("review_not_found", "GET", "/api/v1/interviews/nonexistent/review", a, None),
    ]


def mask_session_id(raw: bytes) -> bytes:
    try:
        d = json.loads(raw)
        if isinstance(d, dict) and "sessionId" in d:
            d["sessionId"] = "<random>"
        return json.dumps(d, ensure_ascii=False).encode()
    except Exception:
        return raw


def record(name, method, path, headers, body):
    req = urllib.request.Request(BASE + path, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as resp:
            status, hdrs, raw = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        status, hdrs, raw = e.code, e.headers, e.read()
    rec = {
        "name": name,
        "method": method,
        "path": path,
        "status": status,
        "response_headers": {k.lower(): v for k, v in hdrs.items() if k.lower() in STABLE_HEADERS},
    }
    if name == "session_with_auth":
        raw = mask_session_id(raw)
    try:
        rec["body"] = json.loads(raw)
    except Exception:
        rec["body"] = {"__raw__": raw.decode("utf-8", "replace")[:500]}
    return rec


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, method, path, headers, body in cases():
        rec = record(name, method, path, headers, body)
        (OUT / f"{name}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"{name:32s} -> {rec['status']}")
        total += 1
    print(f"recorded {total} cases -> {OUT}")


if __name__ == "__main__":
    main()
