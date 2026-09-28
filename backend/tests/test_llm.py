"""P2 LLM gateway tests (schema / decode / chat_json / retry / multimodal)."""
import json

import httpx
import pytest

from app.llm import Client, Config, ImageInput, Message, Role, decode_json, schema_for
from app.llm.schema import StrictModel


class Nested(StrictModel):
    label: str


class SampleOutput(StrictModel):
    title: str
    score: int
    tags: list[str]
    optional_note: str | None = None
    nested: Nested


def make_client(handler) -> Client:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return Client(Config(api_key="test-key", base_url="https://example.com/v1", retry_base_wait=0), http)


# --- schema ---

def test_schema_for_all_required_and_optional_null():
    schema = schema_for(SampleOutput)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["title", "score", "tags", "optional_note", "nested"]
    assert schema["properties"]["title"] == {"type": "string"}
    assert schema["properties"]["score"] == {"type": "integer"}
    assert schema["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}
    assert schema["properties"]["optional_note"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert schema["properties"]["nested"] == {
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "additionalProperties": False,
        "required": ["label"],
    }


# --- decode ---

def test_decode_json_rejects_null():
    with pytest.raises(ValueError):
        decode_json("null", SampleOutput)


def test_decode_json_rejects_trailing():
    with pytest.raises(ValueError):
        decode_json('{"title":"x","score":1,"tags":[],"nested":{"label":"a"}} {"x":1}', SampleOutput)


def test_decode_json_rejects_unknown_field():
    with pytest.raises(ValueError):
        decode_json('{"title":"x","score":1,"tags":[],"nested":{"label":"a"},"bogus":1}', SampleOutput)


# --- chat_json ---

VALID = {"title": "x", "score": 5, "tags": ["a"], "optional_note": None, "nested": {"label": "n"}}


async def test_chat_json_happy_path():
    async def handler(request):
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["max_tokens"] == 4096
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(VALID)}}]})

    result = await make_client(handler).chat_json([Message(Role.USER, "hi")], SampleOutput)
    assert result.title == "x" and result.score == 5 and result.nested.label == "n"


async def test_chat_json_fallback_to_json_object():
    formats = []

    async def handler(request):
        body = json.loads(request.content)
        formats.append(body["response_format"])
        if body["response_format"].get("type") == "json_schema":
            return httpx.Response(400, json={"error": {"message": "model does not support response_format json_schema"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(VALID)}}]})

    result = await make_client(handler).chat_json([Message(Role.USER, "hi")], SampleOutput)
    assert result.title == "x"
    assert formats[0]["type"] == "json_schema" and formats[1] == {"type": "json_object"}


async def test_chat_json_repairs_invalid_json():
    message_counts = []

    async def handler(request):
        body = json.loads(request.content)
        message_counts.append(len(body["messages"]))
        if len(message_counts) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": "{not valid json"}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(VALID)}}]})

    result = await make_client(handler).chat_json([Message(Role.USER, "hi")], SampleOutput)
    assert result.title == "x"
    assert message_counts == [1, 3]  # original + assistant echo + fix instruction


async def test_chat_json_retries_on_429():
    calls = []

    async def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(VALID)}}]})

    result = await make_client(handler).chat_json([Message(Role.USER, "hi")], SampleOutput)
    assert result.title == "x"
    assert len(calls) == 2


# --- multimodal ---

async def test_chat_json_with_images_attaches_image_url():
    captured = {}

    async def handler(request):
        body = json.loads(request.content)
        captured["messages"] = body["messages"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(VALID)}}]})

    await make_client(handler).chat_json_with_images(
        [Message(Role.SYSTEM, "sys"), Message(Role.USER, "look")],
        [ImageInput(url="data:image/png;base64,AAAA")],
        SampleOutput,
    )
    last = captured["messages"][-1]
    assert last["content"] == [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "high"}},
    ]
