"""Entrypoint: load env, assemble config, run uvicorn (mirrors cmd/offerpilot-api/main.go)."""
from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from . import config
from .api import AppConfig, create_app
from .knowledge import load as load_knowledge


def resolve_knowledge_directory(configured: str) -> Path:
    path = Path(configured)
    if path.is_absolute():
        if path.is_dir():
            return path
        raise ValueError(f"{configured} does not exist or is not a directory")
    cwd = Path.cwd()
    for candidate in (cwd / configured, cwd.parent / configured):
        if candidate.is_dir():
            return candidate.resolve()
    raise ValueError(f"{configured} not found from {cwd} or its parent")


def build_config() -> AppConfig:
    config.load_env()
    model_configured = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    speech_configured = bool(os.environ.get("MIMO_API_KEY", "").strip())
    auth_required = (
        os.environ.get("NODE_ENV", "").strip().lower() == "production"
        or config.bool_env("OFFERPILOT_REQUIRE_AUTH", False)
    )
    knowledge_dir = resolve_knowledge_directory(config.env_or("KNOWLEDGE_DIR", "knowledge"))
    index = load_knowledge(str(knowledge_dir))
    return AppConfig(
        version=config.VERSION,
        api_key=os.environ.get("OFFERPILOT_API_KEY", ""),
        require_auth=auth_required,
        allowed_origins=config.csv_env(
            "OFFERPILOT_ALLOWED_ORIGINS", ["http://localhost:3000", "http://127.0.0.1:3000"]
        ),
        model_configured=model_configured,
        speech_configured=speech_configured,
        knowledge_entries=index.len(),
        knowledge_index=index,
        max_json_body_bytes=config.int_env("OFFERPILOT_MAX_JSON_BODY_BYTES", 256 << 10),
        max_interview_bytes=config.int_env("OFFERPILOT_MAX_INTERVIEW_BODY_BYTES", 2 << 20),
        max_audio_body_bytes=config.int_env("OFFERPILOT_MAX_AUDIO_BODY_BYTES", 25 << 20),
        max_resume_diagnosis_bytes=config.int_env("OFFERPILOT_MAX_RESUME_DIAGNOSIS_BODY_BYTES", 12 << 20),
        max_message_chars=config.int_env("OFFERPILOT_MAX_MESSAGE_CHARS", 20000),
        max_tts_text_chars=config.int_env("OFFERPILOT_MAX_TTS_TEXT_CHARS", 5000),
        max_url_chars=config.int_env("OFFERPILOT_MAX_URL_CHARS", 4096),
    )


def main() -> None:
    app_config = build_config()
    app = create_app(app_config)
    port = config.int_env("PORT", 3001)
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
