"""LLM model-boundary types mirroring Go llm/types.go."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class Message:
    role: Role
    content: str


@dataclass
class ImageInput:
    url: str
    detail: str = ""
