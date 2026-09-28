"""Function tools mirroring Go harness/tool.go (Pydantic single-source schema)."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..llm import schema_for

TOOL_RISK_READ = "read"
TOOL_RISK_EXTERNAL_READ = "external_read"

Handler = Callable[[dict], Awaitable[dict]]


class FunctionTool:
    def __init__(
        self,
        name: str,
        description: str,
        input_model: type,
        handler: Handler,
        risk: str = TOOL_RISK_READ,
        timeout: float = 0.0,
    ):
        self.name = name
        self.description = description
        self.input_model = input_model
        self.handler = handler
        self.risk = risk
        self.timeout = timeout

    def input_schema(self) -> dict:
        return schema_for(self.input_model)
