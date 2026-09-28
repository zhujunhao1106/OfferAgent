"""Smoke tests for P0 skeleton: health, auth, CORS."""
from fastapi.testclient import TestClient

from app.api import AppConfig, create_app


def make_client(**overrides) -> TestClient:
    config = AppConfig(**overrides)
    return TestClient(create_app(config))


def test_health_ok():
    client = make_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "offerpilot-go"
    assert body["live"] is True
    assert body["status"] == "ok"
    assert resp.headers["content-type"] == "application/json; charset=utf-8"
    assert resp.headers["cache-control"] == "no-store"


def test_health_live():
    client = make_client()
    assert client.get("/health/live").json()["status"] == "live"


def test_health_ready_unconfigured():
    client = make_client(model_configured=False)
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    assert body["readiness"] == "not_ready"
    assert body["crawlerConfigured"] is False
    assert body["matcherConfigured"] is False
    assert body["resumeDiagnosticianConfigured"] is False


def test_health_ready_configured():
    client = make_client(model_configured=True)
    assert client.get("/health/ready").status_code == 200


def test_session_requires_auth():
    client = make_client(api_key="test-key", require_auth=True)
    resp = client.post("/api/session")
    assert resp.status_code == 401
    assert resp.json() == {
        "error": {"code": "unauthorized", "message": "Unauthorized", "retryable": False}
    }


def test_session_with_auth():
    client = make_client(api_key="test-key", require_auth=True)
    resp = client.post("/api/session", headers={"Authorization": "Bearer test-key"})
    assert resp.status_code == 200
    assert resp.json()["sessionId"]


def test_session_bad_origin():
    client = make_client(api_key="test-key", require_auth=True, allowed_origins=["http://allowed.example"])
    resp = client.post(
        "/api/session",
        headers={"Authorization": "Bearer test-key", "Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "origin_not_allowed"
    # Go returns 403 before setting CORS allow-methods/allow-headers, but after
    # writeAPIError sets Cache-Control: no-store.
    assert resp.headers["cache-control"] == "no-store"
    assert "access-control-allow-methods" not in resp.headers
    assert "access-control-allow-origin" not in resp.headers


def test_options_preflight():
    client = make_client(allowed_origins=["http://allowed.example"])
    resp = client.options(
        "/api/session",
        headers={"Origin": "http://allowed.example", "Access-Control-Request-Method": "POST"},
    )
    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == "http://allowed.example"


def test_global_500_handler_does_not_swallow_404():
    app = create_app(AppConfig())

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json() == {
        "error": {"code": "internal", "message": "Internal server error", "retryable": True}
    }

    missing = client.get("/nonexistent")
    assert missing.status_code == 404
