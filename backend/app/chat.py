"""OpenAI-compatible streaming chat client mirroring Go chat/client.go."""
from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass

import httpx


@dataclass
class Config:
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    timeout: float = 90.0
    max_tokens: int = 4096


@dataclass
class Message:
    role: str
    content: str


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Delta:
    text: str = ""
    thinking: str = ""


class Client:
    def __init__(self, config: Config, http_client: httpx.AsyncClient | None = None):
        config.api_key = config.api_key.strip()
        if not config.api_key:
            raise ValueError("chat: OPENAI_API_KEY is required")
        config.base_url = config.base_url.strip().rstrip("/") or "https://api.openai.com/v1"
        config.model = config.model.strip() or "gpt-4o-mini"
        if config.timeout <= 0:
            config.timeout = 90.0
        if config.max_tokens <= 0:
            config.max_tokens = 4096
        self._config = config
        self._http = http_client or httpx.AsyncClient()

    async def stream(self, model: str, messages: list[Message], on_delta) -> Usage:
        if not messages:
            raise ValueError("chat: at least one message is required")
        if not model.strip():
            model = self._config.model
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": self._config.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        headers = {
            "Authorization": "Bearer " + self._config.api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
        }
        body = json.dumps(payload, ensure_ascii=False).encode()
        try:
            async with asyncio.timeout(self._config.timeout):
                async with self._http.stream("POST", self._config.base_url + "/chat/completions", headers=headers, content=body) as resp:
                    if resp.status_code < 200 or resp.status_code >= 300:
                        data = (await resp.aread())[:4097]
                        raise ValueError(f"chat: provider HTTP {resp.status_code}: {_compact(data.decode('utf-8', 'replace'), 4000)}")
                    if "text/event-stream" in resp.headers.get("content-type", "").lower():
                        return await self._read_event_stream(resp, on_delta)
                    return await self._read_json_completion(resp, on_delta)
        except TimeoutError as exc:
            raise TimeoutError(f"chat: request timeout after {self._config.timeout}s") from exc

    async def _read_event_stream(self, resp, on_delta) -> Usage:
        usage = Usage()
        async for raw_line in resp.aiter_lines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data or data == "[DONE]":
                continue
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError as exc:
                raise ValueError(f"chat: decode stream chunk: {exc}") from exc
            error = chunk.get("error")
            if isinstance(error, dict) and error.get("message"):
                raise ValueError("chat: provider stream error: " + error["message"])
            usage_obj = chunk.get("usage") or {}
            if usage_obj.get("prompt_tokens") or usage_obj.get("completion_tokens"):
                usage = Usage(
                    input_tokens=usage_obj.get("prompt_tokens", 0),
                    output_tokens=usage_obj.get("completion_tokens", 0),
                )
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta_obj = (choices[0].get("delta") or {})
            delta = Delta(
                text=_decode_text(delta_obj.get("content")),
                thinking=(delta_obj.get("reasoning_content") or "").strip(),
            )
            if (delta.text or delta.thinking) and on_delta:
                await _invoke_delta(on_delta, delta)
        return usage

    async def _read_json_completion(self, resp, on_delta) -> Usage:
        data = (await resp.aread())[: 8 << 20]
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"chat: decode completion: {exc}") from exc
        error = chunk.get("error")
        if isinstance(error, dict) and error.get("message"):
            raise ValueError("chat: provider error: " + error["message"])
        choices = chunk.get("choices") or []
        if not choices:
            raise ValueError("chat: provider response has no choices")
        text = _decode_text((choices[0].get("message") or {}).get("content"))
        if not text:
            raise ValueError("chat: provider returned empty content")
        if on_delta:
            await _invoke_delta(on_delta, Delta(text=text))
        usage_obj = chunk.get("usage") or {}
        return Usage(
            input_tokens=usage_obj.get("prompt_tokens", 0),
            output_tokens=usage_obj.get("completion_tokens", 0),
        )


async def _invoke_delta(on_delta, delta: Delta) -> None:
    result = on_delta(delta)
    if inspect.isawaitable(result):
        await result


def _decode_text(content) -> str:
    if content is None or content == "" or content == "null":
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        result = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("", "text", "output_text"):
                result.append(part.get("text", ""))
        return "".join(result)
    return ""


def _compact(value: str, limit: int) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[:limit] + "..."
