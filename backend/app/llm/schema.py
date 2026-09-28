"""Structured-output schema + strict decoding mirroring Go llm/json.go."""
from __future__ import annotations

import json
from enum import Enum
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Output models must forbid extra fields to match Go's DisallowUnknownFields."""

    model_config = ConfigDict(extra="forbid")


def schema_for(model: type[BaseModel]) -> dict:
    """Reflection-free JSON Schema mirroring Go schemaForType semantics:
    object + additionalProperties:false + ALL fields required; optionality is
    expressed only via anyOf[T, {"type":"null"}] for Optional fields."""
    return _schema_model(model, set())


def _schema_model(model: type[BaseModel], visiting: set) -> dict:
    if model in visiting:
        raise ValueError(f"llm: recursive output type {model.__name__} is not supported")
    visiting.add(model)
    try:
        hints = get_type_hints(model)
        properties: dict = {}
        required: list[str] = []
        for name, info in model.model_fields.items():
            annotation = hints.get(name, info.annotation)
            prop = _schema_annotation(annotation, visiting)
            if info.description and info.description.strip():
                prop = {**prop, "description": info.description.strip()}
            properties[name] = prop
            required.append(name)
        schema: dict = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = required
        return schema
    finally:
        visiting.discard(model)


def _schema_annotation(annotation, visiting: set) -> dict:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, UnionType):
        non_none = [a for a in args if a is not type(None)]
        if type(None) in args and len(non_none) == 1:
            inner = _schema_annotation(non_none[0], visiting)
            return {"anyOf": [inner, {"type": "null"}]}
        raise ValueError(f"llm: union type {annotation} is not supported")
    if origin is list:
        return {"type": "array", "items": _schema_annotation(args[0], visiting)}
    if origin is dict:
        if args[0] is not str:
            raise ValueError(f"llm: map key {args[0]} is not supported")
        return {"type": "object", "additionalProperties": _schema_annotation(args[1], visiting)}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _schema_model(annotation, visiting)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if issubclass(annotation, str):
            return {"type": "string"}
        if issubclass(annotation, int):
            return {"type": "integer"}
        raise ValueError(f"llm: enum {annotation.__name__} must be str- or int-based")
    if annotation is str:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is Any:
        return {}
    raise ValueError(f"llm: output type {annotation} is not JSON schema compatible")


def decode_json(raw: str, out_model: type[BaseModel]) -> BaseModel:
    """One JSON value, unknown fields rejected, null rejected, no trailing data."""
    stripped = raw.strip()
    if stripped == "null":
        raise ValueError("llm: structured response must not be null")
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"llm: decode structured response: {exc}") from exc
    return out_model.model_validate(data)


def compact_for_prompt(raw: str, limit: int) -> str:
    raw = raw.strip()
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "...[truncated]"
