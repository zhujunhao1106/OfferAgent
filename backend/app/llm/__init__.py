"""OpenAI-compatible LLM gateway (mirrors Go internal/llm)."""
from .client import Client, HTTPError
from .config import Config, normalized
from .schema import StrictModel, compact_for_prompt, decode_json, schema_for
from .types import ImageInput, Message, Role

__all__ = [
    "Client", "HTTPError",
    "Config", "normalized",
    "StrictModel", "compact_for_prompt", "decode_json", "schema_for",
    "ImageInput", "Message", "Role",
]
