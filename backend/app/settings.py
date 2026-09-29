"""Single source of truth for runtime configuration (mirrors Go config + main.go).

Every URL, API key, model name, timeout, and limit is read here exactly once
from the environment. Business modules receive plain values built from this
module instead of reading os.environ themselves.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config

_DURATION_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)(ns|us|µs|ms|s|m|h)")
_DURATION_UNITS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}


@dataclass
class Settings:
    # --- server ---
    version: str = config.VERSION
    server_api_key: str = ""
    require_auth: bool = False
    allowed_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    # --- text model (OpenAI-compatible) ---
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_timeout: float = 90.0
    openai_max_retries: int = 2
    openai_retry_base_wait: float = 0.25
    openai_max_tokens: int = 4096
    # Go main.go gives the chat client its own model fallback and timeout key.
    chat_model: str = "gpt-5.5"
    chat_timeout: float = 90.0

    # --- speech (MiMo) ---
    mimo_api_key: str = ""
    mimo_base_url: str = "https://api.xiaomimimo.com/v1"
    mimo_asr_model: str = "mimo-v2.5-asr"
    mimo_tts_model: str = "mimo-v2.5-tts"
    mimo_tts_voice: str = "mimo_default"
    mimo_asr_language: str = "auto"
    mimo_timeout: float = 60.0

    # --- knowledge / storage ---
    knowledge_dir: str = "knowledge"
    db_path: str = "data/offerpilot.db"
    interview_knowledge_limit: int = 8

    # --- harness ---
    harness_max_concurrent: int = 4
    harness_trace_capacity: int = 512
    interviewer_timeout: float = 90.0
    assessor_timeout: float = 180.0
    reporter_timeout: float = 90.0
    planner_timeout: float = 90.0
    # Fixed 5-minute wall clock for the detached stream run (mirrors Go
    # httpapi.InterviewRunTimeout, which is a constant and not env-driven).
    interview_run_timeout: float = 300.0

    # --- domain agents ---
    matcher_timeout: float = 90.0
    resume_diagnostician_timeout: float = 120.0

    # --- web crawler ---
    crawler_request_timeout: float = 10.0
    crawler_max_response_bytes: int = 2 << 20
    crawler_max_redirects: int = 3
    crawler_allow_benchmark_tunnel: bool = False
    crawler_fallback_max_iterations: int = 4
    crawler_fallback_timeout: float = 90.0
    crawler_decision_timeout: float = 60.0

    # --- request limits ---
    max_json_body_bytes: int = 256 << 10
    max_interview_bytes: int = 2 << 20
    max_audio_body_bytes: int = 25 << 20
    max_resume_diagnosis_bytes: int = 12 << 20
    max_message_chars: int = 20000
    max_tts_text_chars: int = 5000
    max_url_chars: int = 4096

    @property
    def model_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def speech_configured(self) -> bool:
        return bool(self.mimo_api_key)


def from_env() -> Settings:
    production = config.env_or("NODE_ENV", "").lower() == "production"
    return Settings(
        version=config.VERSION,
        server_api_key=config.env_or("OFFERPILOT_API_KEY", ""),
        require_auth=production or config.bool_env("OFFERPILOT_REQUIRE_AUTH", False),
        allowed_origins=config.csv_env(
            "OFFERPILOT_ALLOWED_ORIGINS", ["http://localhost:3000", "http://127.0.0.1:3000"]
        ),
        openai_api_key=config.env_or("OPENAI_API_KEY", ""),
        openai_base_url=config.env_or("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        openai_model=config.env_or("OPENAI_MODEL", "gpt-4o-mini"),
        openai_timeout=_duration_or_millis("OPENAI_TIMEOUT", 90.0),
        openai_max_retries=config.int_env("OPENAI_MAX_RETRIES", 2),
        openai_retry_base_wait=_duration_or_millis("OPENAI_RETRY_BASE_WAIT", 0.25),
        openai_max_tokens=config.int_env("OPENAI_MAX_TOKENS", 4096),
        chat_model=config.env_or("OPENAI_MODEL", "gpt-5.5"),
        chat_timeout=_go_duration("OPENAI_CHAT_TIMEOUT", 90.0),
        mimo_api_key=config.env_or("MIMO_API_KEY", ""),
        mimo_base_url=config.env_or("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1"),
        mimo_asr_model=config.env_or("MIMO_ASR_MODEL", "mimo-v2.5-asr"),
        mimo_tts_model=config.env_or("MIMO_TTS_MODEL", "mimo-v2.5-tts"),
        mimo_tts_voice=config.env_or("MIMO_TTS_VOICE", "mimo_default"),
        mimo_asr_language=config.env_or("MIMO_ASR_LANGUAGE", "auto"),
        mimo_timeout=_go_duration("MIMO_TIMEOUT", 60.0),
        knowledge_dir=config.env_or("KNOWLEDGE_DIR", "knowledge"),
        db_path=config.env_or("DB_PATH", "data/offerpilot.db"),
        interview_knowledge_limit=config.int_env("OFFERPILOT_INTERVIEW_KNOWLEDGE_LIMIT", 8),
        harness_max_concurrent=config.int_env("OFFERPILOT_HARNESS_MAX_CONCURRENT", 4),
        harness_trace_capacity=config.int_env("OFFERPILOT_HARNESS_TRACE_CAPACITY", 512),
        interviewer_timeout=_duration_or_millis("OFFERPILOT_INTERVIEWER_TIMEOUT", 90.0),
        assessor_timeout=_duration_or_millis("OFFERPILOT_ASSESSOR_TIMEOUT", 180.0),
        reporter_timeout=_duration_or_millis("OFFERPILOT_REPORTER_TIMEOUT", 90.0),
        planner_timeout=_duration_or_millis("OFFERPILOT_PLANNER_TIMEOUT", 90.0),
        matcher_timeout=_go_duration("OFFERPILOT_MATCHER_TIMEOUT", 90.0),
        resume_diagnostician_timeout=_go_duration("OFFERPILOT_RESUME_DIAGNOSTICIAN_TIMEOUT", 120.0),
        crawler_request_timeout=_go_duration("OFFERPILOT_CRAWLER_REQUEST_TIMEOUT", 10.0),
        crawler_max_response_bytes=config.int_env("OFFERPILOT_CRAWLER_MAX_RESPONSE_BYTES", 2 << 20),
        crawler_max_redirects=config.int_env("OFFERPILOT_CRAWLER_MAX_REDIRECTS", 3),
        crawler_allow_benchmark_tunnel=config.bool_env("OFFERPILOT_ALLOW_TUN_FAKE_IP", not production),
        crawler_fallback_max_iterations=config.int_env("OFFERPILOT_CRAWLER_FALLBACK_MAX_ITERATIONS", 4),
        crawler_fallback_timeout=_go_duration("OFFERPILOT_CRAWLER_FALLBACK_TIMEOUT", 90.0),
        crawler_decision_timeout=_go_duration("OFFERPILOT_CRAWLER_DECISION_TIMEOUT", 60.0),
        max_json_body_bytes=config.int_env("OFFERPILOT_MAX_JSON_BODY_BYTES", 256 << 10),
        max_interview_bytes=config.int_env("OFFERPILOT_MAX_INTERVIEW_BODY_BYTES", 2 << 20),
        max_audio_body_bytes=config.int_env("OFFERPILOT_MAX_AUDIO_BODY_BYTES", 25 << 20),
        max_resume_diagnosis_bytes=config.int_env("OFFERPILOT_MAX_RESUME_DIAGNOSIS_BODY_BYTES", 12 << 20),
        max_message_chars=config.int_env("OFFERPILOT_MAX_MESSAGE_CHARS", 20000),
        max_tts_text_chars=config.int_env("OFFERPILOT_MAX_TTS_TEXT_CHARS", 5000),
        max_url_chars=config.int_env("OFFERPILOT_MAX_URL_CHARS", 4096),
    )


def _parse_go_duration(value: str) -> float | None:
    total = 0.0
    consumed = 0
    for match in _DURATION_RE.finditer(value):
        if match.start() != consumed:
            return None
        total += float(match.group(1)) * _DURATION_UNITS[match.group(2)]
        consumed = match.end()
    if consumed != len(value) or consumed == 0:
        return None
    return total


def _go_duration(key: str, fallback: float) -> float:
    """Go time.ParseDuration only (no bare-millisecond fallback)."""
    value = config.env_or(key, "")
    if not value:
        return fallback
    parsed = _parse_go_duration(value)
    return parsed if parsed is not None and parsed > 0 else fallback


def _duration_or_millis(key: str, fallback: float) -> float:
    """llm.Config durationEnv: Go duration string, or a bare number in milliseconds."""
    value = config.env_or(key, "")
    if not value:
        return fallback
    parsed = _parse_go_duration(value)
    if parsed is not None and parsed > 0:
        return parsed
    if value.isdigit():
        millis = int(value)
        if millis > 0:
            return millis / 1000.0
    return fallback
