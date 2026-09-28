"""LLM client configuration mirroring Go llm/config.go."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 90.0  # seconds


@dataclass
class Config:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = 2
    retry_base_wait: float = 0.25
    max_tokens: int = 4096
    temperature: float | None = None


def from_env() -> Config:
    return Config(
        api_key=_env_or("OPENAI_API_KEY", ""),
        base_url=_env_or("OPENAI_BASE_URL", DEFAULT_BASE_URL),
        model=_env_or("OPENAI_MODEL", DEFAULT_MODEL),
        timeout=_duration_env("OPENAI_TIMEOUT", DEFAULT_TIMEOUT),
        max_retries=_int_env("OPENAI_MAX_RETRIES", 2),
        retry_base_wait=_duration_env("OPENAI_RETRY_BASE_WAIT", 0.25),
        max_tokens=_int_env("OPENAI_MAX_TOKENS", 4096),
    )


def normalized(config: Config) -> Config:
    config.api_key = config.api_key.strip()
    config.base_url = config.base_url.strip().rstrip("/")
    config.model = config.model.strip()
    if not config.api_key:
        raise ValueError("llm: OPENAI_API_KEY is required")
    if not config.base_url:
        config.base_url = DEFAULT_BASE_URL
    if not config.model:
        config.model = DEFAULT_MODEL
    if config.timeout <= 0:
        config.timeout = DEFAULT_TIMEOUT
    if config.max_retries < 0:
        config.max_retries = 0
    if config.retry_base_wait <= 0:
        config.retry_base_wait = 0.25
    if config.max_tokens <= 0:
        config.max_tokens = 4096
    return config


def _env_or(key: str, fallback: str) -> str:
    value = os.environ.get(key, "").strip()
    return value if value else fallback


def _int_env(key: str, fallback: int) -> int:
    try:
        return int(os.environ.get(key, "").strip())
    except ValueError:
        return fallback


_DURATION_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(ms|s|m|h)$")


def _duration_env(key: str, fallback: float) -> float:
    """Go duration string, or a bare integer interpreted as milliseconds."""
    value = os.environ.get(key, "").strip()
    if not value:
        return fallback
    match = _DURATION_RE.fullmatch(value)
    if match:
        num = float(match.group(1))
        unit = match.group(2)
        return {"ms": num / 1000.0, "s": num, "m": num * 60.0, "h": num * 3600.0}[unit]
    if value.isdigit():
        return int(value) / 1000.0
    return fallback
