"""NDJSON trace-stream contract for /api/interview/stream."""
import json

from fastapi.testclient import TestClient

from app.api import AppConfig, create_app
from app.interview import MemoryStore, Service

from test_api_interview import _Clock, _FakeAgent, _IDs


def make_client(**overrides):
    service = Service(agent=_FakeAgent(), store=MemoryStore(), ids=_IDs(), clock=_Clock())
    config = {
        "model_configured": True,
        "version": "0.4.1",
        "knowledge_entries": 486,
        "interview": service,
    }
    config.update(overrides)
    app = create_app(AppConfig(**config))
    return TestClient(app, raise_server_exceptions=False)


def _lines(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson"), response.headers["content-type"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_stream_start_emits_trace_then_result():
    client = make_client()
    response = client.post(
        "/api/interview/stream",
        headers={"Accept": "application/x-ndjson"},
        json={
            "action": "start", "clientSessionId": "client-1",
            "config": {"questionCount": 1, "focus": "mixed"},
            "materials": {"jd": "高级 Go 工程师"},
        },
    )
    lines = _lines(response)
    result = lines[-1]
    assert result["type"] == "result"
    assert result["status"] == 200
    assert result["data"]["interviewId"]
    traces = [line for line in lines if line["type"] == "trace"]
    assert traces, "expected at least one trace event"
    first = traces[0]["trace"]
    assert first["stage"] == "request"
    assert first["label"] == "Execute interview action"
    assert "agent" not in first
    statuses = [t["trace"]["status"] for t in traces]
    assert statuses[0] == "queued"
    assert "running" in statuses
    assert statuses[-1] == "completed"


def test_stream_invalid_action_result_400():
    client = make_client()
    response = client.post("/api/interview/stream", json={"action": "bogus"})
    lines = _lines(response)
    result = lines[-1]
    assert result["type"] == "result"
    assert result["status"] == 400
    assert result["data"]["error"]["code"] == "validation"
    assert result["data"]["error"]["field"] == "action"
    traces = [line for line in lines if line["type"] == "trace"]
    assert traces[-1]["trace"]["status"] == "failed"


def test_stream_unconfigured_result_503():
    app = create_app(AppConfig())
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/interview/stream", json={"action": "start"})
    lines = _lines(response)
    result = lines[-1]
    assert result["type"] == "result"
    assert result["status"] == 503
    assert result["data"]["error"]["code"] == "service_unavailable"


def test_stream_body_too_large_single_result():
    client = make_client(max_interview_bytes=16)
    response = client.post("/api/interview/stream", json={"action": "start", "pad": "x" * 1000})
    lines = _lines(response)
    assert len(lines) == 1
    assert lines[0]["type"] == "result"
    assert lines[0]["status"] == 413
    assert lines[0]["data"]["error"]["code"] == "body_too_large"
