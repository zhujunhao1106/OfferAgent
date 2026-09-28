"""Application factory (mirrors Go httpapi.New + route registration)."""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Depends, FastAPI, Request

from ..errors import ApiError, json_response
from ..middleware import Security
from ..session import Store


@dataclass
class AppConfig:
    version: str = "dev"
    api_key: str = ""
    require_auth: bool = False
    allowed_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    model_configured: bool = False
    speech_configured: bool = False
    knowledge_entries: int = 0
    knowledge_index: object | None = None
    crawler: object | None = None
    matcher: object | None = None
    resume_diagnostician: object | None = None
    max_json_body_bytes: int = 256 << 10
    max_interview_bytes: int = 2 << 20
    max_audio_body_bytes: int = 25 << 20
    max_resume_diagnosis_bytes: int = 12 << 20
    max_message_chars: int = 20000
    max_tts_text_chars: int = 5000
    max_url_chars: int = 4096


def health_payload(config: AppConfig, status: str) -> dict:
    harness = "ready" if config.model_configured else "not_ready"
    return {
        "status": status,
        "service": "offerpilot-go",
        "version": config.version,
        "live": True,
        "ready": config.model_configured,
        "readiness": harness,
        "harness": harness,
        "modelConfigured": config.model_configured,
        "speechConfigured": config.speech_configured,
        "crawlerConfigured": config.crawler is not None,
        "matcherConfigured": config.matcher is not None,
        "resumeDiagnosticianConfigured": config.resume_diagnostician is not None,
        "knowledgeEntries": config.knowledge_entries,
    }


def create_app(config: AppConfig) -> FastAPI:
    security = Security(config.api_key, config.require_auth, config.allowed_origins)
    sessions = Store()

    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError):
        return json_response(exc.payload(), exc.status)

    app.middleware("http")(security.middleware)

    @app.get("/health")
    async def health():
        return json_response(health_payload(config, "ok"))

    @app.get("/health/live")
    async def health_live():
        return json_response(health_payload(config, "live"))

    @app.get("/health/ready")
    async def health_ready():
        if not config.model_configured:
            return json_response(health_payload(config, "not_ready"), 503)
        return json_response(health_payload(config, "ready"))

    @app.post("/api/session", dependencies=[Depends(security.authenticate)])
    async def create_session():
        session = sessions.create()
        return json_response({"sessionId": session.id})

    return app
