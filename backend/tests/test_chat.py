"""P3 chat (streaming SSE) tests."""
import json

import httpx
import pytest

from app.chat import Client, Config, Delta, Message


def make_client(handler) -> Client:
    return Client(Config(api_key="k", base_url="https://example.com/v1"), httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_stream_sse():
    sse = (
        'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
        'data: {"choices":[{"delta":{"reasoning_content":"思考中"}}]}\n\n'
        'data: {"choices":[]}\n\n'
        'data: {"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
        'data: [DONE]\n\n'
    )

    async def handler(request):
        assert request.headers["accept"] == "text/event-stream, application/json"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())

    deltas = []
    usage = await make_client(handler).stream("", [Message(role="user", content="hi")], deltas.append)
    assert deltas == [Delta(text="你好"), Delta(thinking="思考中")]
    assert usage.input_tokens == 5 and usage.output_tokens == 2


async def test_stream_json_fallback():
    async def handler(request):
        body = {"choices": [{"message": {"content": "你好"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return httpx.Response(200, headers={"content-type": "application/json"}, content=json.dumps(body).encode())

    deltas = []
    usage = await make_client(handler).stream("", [Message("user", "hi")], deltas.append)
    assert deltas == [Delta(text="你好")]
    assert usage.input_tokens == 1 and usage.output_tokens == 1


async def test_stream_http_error():
    async def handler(request):
        return httpx.Response(500, content=b'{"error":{"message":"boom"}}')

    with pytest.raises(ValueError, match="provider HTTP 500"):
        await make_client(handler).stream("", [Message("user", "hi")], None)


async def test_stream_requires_messages():
    with pytest.raises(ValueError):
        await make_client(_unused).stream("", [], None)


async def _unused(request):
    raise AssertionError("unexpected HTTP call")
