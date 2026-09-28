"""Golden contract test: diff recorded Go responses against the Python backend.

Golden files live at tests/golden/api/*.json (repo root). Four cases encode the
Go typed-nil bug (§2.6) and are asserted against the intended fixed behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import AppConfig, create_app

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "tests" / "golden" / "api"


class _FakeMatcher:
    async def match(self, request):
        raise AssertionError("matcher should not be reached")


class _FakeCrawler:
    async def crawl(self, request):
        raise AssertionError("crawler should not be reached")


class _FakeDiagnostician:
    async def diagnose(self, request):
        raise AssertionError("diagnostician should not be reached")


class _FakeChat:
    async def stream(self, model, messages, on_delta):
        raise AssertionError("chat should not be reached")


def _interview_service():
    from app.interview import MemoryStore, Service

    return Service(agent=None, store=MemoryStore())


def _health(**overrides):
    config = {"version": "0.4.1", "knowledge_entries": 486}
    config.update(overrides)
    return config


CASES = {
    # health
    "health_ok": (_health(model_configured=True), "GET", "/health", None, {}),
    "health_live": (_health(model_configured=True), "GET", "/health/live", None, {}),
    "health_ready": (_health(model_configured=True), "GET", "/health/ready", None, {}),
    "health_ready_unconfigured": (_health(model_configured=False), "GET", "/health/ready", None, {}),
    # session + auth + CORS
    "session_no_auth": ({"api_key": "test-key", "require_auth": True}, "POST", "/api/session", None, {}),
    "session_with_auth": ({"api_key": "test-key", "require_auth": True}, "POST", "/api/session", None, {"Authorization": "Bearer test-key"}),
    "session_bad_origin": (
        {"api_key": "test-key", "require_auth": True, "allowed_origins": ["http://allowed.example"]},
        "POST", "/api/session", None, {"Origin": "https://evil.example"},
    ),
    "options_preflight": ({"allowed_origins": ["http://allowed.example"]}, "OPTIONS", "/api/session", None, {"Origin": "http://allowed.example"}),
    # chat
    "chat_empty_message": ({"chat": _FakeChat()}, "POST", "/api/chat", {"message": ""}, {}),
    "chat_invalid_json": ({"chat": _FakeChat()}, "POST", "/api/chat", b"not json", {}),
    "chat_message_too_long": ({"chat": _FakeChat()}, "POST", "/api/chat", {"message": "a" * 20001}, {}),
    "chat_unconfigured": ({}, "POST", "/api/chat", {"message": "hi"}, {}),
    "chat_unknown_field": ({"chat": _FakeChat()}, "POST", "/api/chat", {"message": "hi", "extra": 1}, {}),
    # match
    "match_missing": ({"matcher": _FakeMatcher()}, "POST", "/api/match", {"jd": "", "resume": ""}, {}),
    "match_unconfigured": ({}, "POST", "/api/match", {"jd": "x", "resume": "y"}, {}),
    "match_unknown_field": ({"matcher": _FakeMatcher()}, "POST", "/api/match", {"jd": "x", "resume": "y", "extra": 1}, {}),
    # resume diagnosis
    "resume_empty": ({"resume_diagnostician": _FakeDiagnostician()}, "POST", "/api/resume/diagnose", {"content": ""}, {}),
    "resume_too_many_images": (
        {"resume_diagnostician": _FakeDiagnostician()},
        "POST", "/api/resume/diagnose", {"content": "x", "images": ["i1", "i2", "i3", "i4"]}, {},
    ),
    "resume_unconfigured": ({}, "POST", "/api/resume/diagnose", {"content": "x"}, {}),
    # crawl
    "crawl_empty_url": ({"crawler": _FakeCrawler()}, "POST", "/api/crawl", {"url": ""}, {}),
    "crawl_unconfigured": ({}, "POST", "/api/crawl", {"url": "https://example.com"}, {}),
    # speech
    "transcribe_empty": ({}, "POST", "/api/transcribe", b"", {}),
    "tts_empty_text": ({}, "POST", "/api/tts", {"text": ""}, {}),
    # interview
    "interview_invalid_action": ({"model_configured": True, "interview": _interview_service()}, "POST", "/api/interview", {"action": "bogus"}, {}),
    "interview_answer_not_found": (
        {"model_configured": True, "interview": _interview_service()},
        "POST", "/api/interview",
        {"action": "answer", "interviewId": "nonexistent", "questionId": "q1", "clientAnswerId": "c1",
         "answer": {"text": "x", "inputMode": "text"}}, {},
    ),
    "interview_report_not_found": (
        {"model_configured": True, "interview": _interview_service()},
        "POST", "/api/interview", {"action": "report", "interviewId": "nonexistent"}, {},
    ),
    "interview_start_bad_count": (
        {"model_configured": True, "interview": _interview_service()},
        "POST", "/api/interview", {"action": "start", "clientSessionId": "c1", "config": {"questionCount": 21}}, {},
    ),
    "interview_unconfigured": ({}, "POST", "/api/interview", {"action": "start"}, {}),
    # recovery
    "snapshot_not_found": ({"model_configured": True, "interview": _interview_service()}, "GET", "/api/v1/interviews/nonexistent", None, {}),
    "review_not_found": ({"model_configured": True, "interview": _interview_service()}, "GET", "/api/v1/interviews/nonexistent/review", None, {}),
    "events_invalid_limit": ({"model_configured": True, "interview": _interview_service()}, "GET", "/api/v1/interviews/nonexistent/events?limit=0", None, {}),
}

_HEALTH_NAMES = {"health_ok", "health_live", "health_ready", "health_ready_unconfigured"}

_UNAVAILABLE_FIXES = {
    "match_unconfigured": (503, {"error": {"code": "matcher_unavailable", "message": "Resume matcher Agent is not configured", "retryable": True}}),
    "resume_unconfigured": (503, {"error": {"code": "resume_diagnostician_unavailable", "message": "Resume diagnostician Agent is not configured", "retryable": True}}),
    "crawl_unconfigured": (503, {"error": {"code": "crawler_unavailable", "message": "Web crawler Agent is not configured", "retryable": True}}),
}


def _expected_status(name: str, golden_status: int) -> int:
    if name in _UNAVAILABLE_FIXES:
        return _UNAVAILABLE_FIXES[name][0]
    return golden_status


def _expected_body(name: str, golden_body):
    if name in _UNAVAILABLE_FIXES:
        return _UNAVAILABLE_FIXES[name][1]
    if name in _HEALTH_NAMES:
        body = dict(golden_body)
        body["crawlerConfigured"] = False
        body["matcherConfigured"] = False
        body["resumeDiagnosticianConfigured"] = False
        return body
    return golden_body


def _matches(expected, actual) -> bool:
    if expected == "<random>":
        return isinstance(actual, str)
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(_matches(expected[k], actual[k]) for k in expected)
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(_matches(e, a) for e, a in zip(expected, actual))
    return expected == actual


@pytest.mark.parametrize("name", sorted(CASES))
def test_golden_contract(name):
    golden = json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    config_overrides, method, path, body, headers = CASES[name]

    client = TestClient(create_app(AppConfig(**config_overrides)), raise_server_exceptions=False)
    if body is None:
        response = client.request(method, path, headers=headers or None)
    elif isinstance(body, (dict, list)):
        response = client.request(method, path, json=body, headers=headers or None)
    else:
        response = client.request(method, path, content=body, headers=headers or None)

    assert response.status_code == _expected_status(name, golden["status"]), f"{name}: status {response.status_code} != {_expected_status(name, golden['status'])}; body={response.text[:500]}"

    expected_body = _expected_body(name, golden["body"])
    if isinstance(expected_body, dict) and "__raw__" in expected_body:
        assert response.text == expected_body["__raw__"]
    else:
        assert _matches(expected_body, response.json()), f"{name}: body mismatch\nactual={json.dumps(response.json(), ensure_ascii=False)}\nexpected={json.dumps(expected_body, ensure_ascii=False)}"

    for key, value in golden.get("response_headers", {}).items():
        assert response.headers.get(key) == value, f"{name}: header {key} = {response.headers.get(key)!r}, want {value!r}"
