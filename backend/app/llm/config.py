"""LLM client configuration value object mirroring Go llm/config.go.

Environment reading lives in app.settings; this module only carries the typed
values the client needs plus normalization fallbacks.
"""
from __future__ import annotations

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
