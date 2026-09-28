"""OpenAI-compatible client mirroring Go llm/client.go."""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from .config import Config, normalized
from .schema import compact_for_prompt, decode_json, decode_json_lenient, schema_for
from .types import ImageInput, Message, Role


class HTTPError(Exception):
    def __init__(self, status_code: int, body: str, retry_after: float):
        self.status_code = status_code
        self.body = body
        self.retry_after = retry_after
        super().__init__(f"llm: provider returned HTTP {status_code}: {body}")


class Client:
    def __init__(self, config: Config, http_client: httpx.AsyncClient | None = None):
        self._config = normalized(config)
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(self._config.timeout))

    async def chat_json(self, messages: list[Message], out_model) -> object:
        schema = schema_for(out_model)
        try:
            content = await self._complete(messages, _strict_response_format(schema))
        except HTTPError as err:
            if not _schema_unsupported(err):
                raise
            # compatibility fallback consumes the single recovery budget
            content = await self._complete(_ensure_json_instruction(messages, schema), {"type": "json_object"})
            return _decode_or_fail_lenient(content, out_model, "after one compatibility fallback")
        try:
            return decode_json(content, out_model)
        except ValueError as decode_err:
            repair = _append_copy(
                messages,
                Message(role=Role.ASSISTANT, content=compact_for_prompt(content, 12000)),
                Message(
                    role=Role.USER,
                    content="Return the same answer again as one valid JSON object matching the required schema. "
                    "Do not use Markdown fences or add commentary. Fix this validation error: " + str(decode_err),
                ),
            )
            repaired = await self._complete(repair, _strict_response_format(schema))
            return _decode_or_fail(repaired, out_model, "after one repair")

    async def chat_json_with_images(self, messages: list[Message], images: list[ImageInput], out_model) -> object:
        if not images:
            return await self.chat_json(messages, out_model)
        schema = schema_for(out_model)
        wire = _multimodal_messages(messages, images)
        try:
            content = await self._complete_messages(wire, _strict_response_format(schema))
        except HTTPError as err:
            if not _schema_unsupported(err):
                raise
            encoded_schema = json.dumps(schema)
            wire = _append_completion_copy(wire, {
                "role": Role.USER.value,
                "content": "Return exactly one JSON object matching this JSON Schema, with no Markdown or commentary:\n" + encoded_schema,
            })
            content = await self._complete_messages(wire, {"type": "json_object"})
            return _decode_or_fail_lenient(content, out_model, "after one compatibility fallback")
        try:
            return decode_json(content, out_model)
        except ValueError as decode_err:
            repair = _append_completion_copy(
                wire,
                {"role": Role.ASSISTANT.value, "content": compact_for_prompt(content, 12000)},
                {
                    "role": Role.USER.value,
                    "content": "Return the same answer again as one valid JSON object matching the required schema. "
                    "Do not use Markdown fences or add commentary. Fix this validation error: " + str(decode_err),
                },
            )
            repaired = await self._complete_messages(repair, _strict_response_format(schema))
            return _decode_or_fail(repaired, out_model, "after one repair")

    async def _complete(self, messages: list[Message], fmt: dict) -> str:
        wire = [{"role": m.role.value, "content": m.content} for m in messages]
        return await self._complete_messages(wire, fmt)

    async def _complete_messages(self, messages: list[dict], fmt: dict) -> str:
        request = {
            "model": self._config.model,
            "messages": messages,
            "response_format": fmt,
            "max_tokens": self._config.max_tokens,
        }
        if self._config.temperature is not None:
            request["temperature"] = self._config.temperature
        body = json.dumps(request, ensure_ascii=False).encode()

        last_err: Exception | None = None
        for attempt in range(self._config.max_retries + 1):
            if attempt > 0:
                await asyncio.sleep(_retry_delay(self._config.retry_base_wait, attempt, last_err))
            try:
                return await self._do_request(body)
            except Exception as err:
                last_err = err
                if not _retryable(err):
                    raise
        raise RuntimeError(f"llm: request failed after {self._config.max_retries + 1} attempts: {last_err}")

    async def _do_request(self, body: bytes) -> str:
        headers = {
            "Authorization": "Bearer " + self._config.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            async with asyncio.timeout(self._config.timeout):
                resp = await self._http.post(self._config.base_url + "/chat/completions", headers=headers, content=body)
        except TimeoutError as exc:
            raise TimeoutError(f"llm: request timeout after {self._config.timeout}s") from exc
        data = resp.content[: 4 << 20]
        if resp.status_code < 200 or resp.status_code >= 300:
            raise HTTPError(
                resp.status_code,
                compact_for_prompt(_provider_error(data), 2000),
                _parse_retry_after(resp.headers.get("Retry-After", "")),
            )
        try:
            decoded = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"llm: decode provider response: {exc}") from exc
        error = decoded.get("error") if isinstance(decoded, dict) else None
        if isinstance(error, dict) and error.get("message"):
            raise ValueError(f"llm: provider error: {error['message']}")
        choices = decoded.get("choices") or []
        if not choices:
            raise ValueError("llm: provider response has no choices")
        content = _decode_content((choices[0].get("message") or {}).get("content"))
        if not content.strip():
            raise ValueError("llm: provider returned empty content")
        return content


def _strict_response_format(schema: dict) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {"name": "structured_response", "strict": True, "schema": schema},
    }


def _ensure_json_instruction(messages: list[Message], schema: dict) -> list[Message]:
    encoded_schema = json.dumps(schema)
    return _append_copy(messages, Message(
        role=Role.USER,
        content="Return exactly one JSON object matching this JSON Schema, with no Markdown or commentary:\n" + encoded_schema,
    ))


def _append_copy(messages: list[Message], *extra: Message) -> list[Message]:
    return [*messages, *extra]


def _append_completion_copy(messages: list[dict], *extra: dict) -> list[dict]:
    return [*messages, *extra]


def _multimodal_messages(messages: list[Message], images: list[ImageInput]) -> list[dict]:
    result = [{"role": m.role.value, "content": m.content} for m in messages]
    last_user = -1
    for index, message in enumerate(messages):
        if message.role == Role.USER:
            last_user = index
    if last_user < 0:
        return result
    parts: list[dict] = [{"type": "text", "text": messages[last_user].content}]
    for image in images:
        detail = image.detail.strip() or "high"
        parts.append({"type": "image_url", "image_url": {"url": image.url, "detail": detail}})
    result[last_user]["content"] = parts
    return result


def _decode_or_fail(content: str, out_model, context: str):
    try:
        return decode_json(content, out_model)
    except ValueError as exc:
        raise ValueError(f"llm: invalid structured response {context}: {exc}") from exc


def _decode_or_fail_lenient(content: str, out_model, context: str):
    try:
        return decode_json_lenient(content, out_model)
    except ValueError as exc:
        raise ValueError(f"llm: invalid structured response {context}: {exc}") from exc


def _decode_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("text", "output_text", ""):
                parts.append(part.get("text", ""))
        return "".join(parts)
    raise ValueError("llm: provider message content is not text")


def _provider_error(data: bytes) -> str:
    try:
        decoded = json.loads(data)
        if isinstance(decoded, dict):
            error = decoded.get("error")
            if isinstance(error, dict) and error.get("message"):
                return error["message"]
    except Exception:
        pass
    return data.decode("utf-8", "replace").strip()


def _retryable(err: Exception) -> bool:
    if isinstance(err, HTTPError):
        return err.status_code == 408 or err.status_code == 429 or err.status_code >= 500
    if isinstance(err, asyncio.CancelledError):
        return False
    if isinstance(err, TimeoutError):
        return True
    return isinstance(err, (httpx.TransportError, OSError))


def _schema_unsupported(err: HTTPError) -> bool:
    if err.status_code != 400:
        return False
    body = err.body.lower()
    return "response_format" in body or "json_schema" in body or "structured output" in body


def _retry_delay(base: float, attempt: int, previous: Exception | None) -> float:
    if isinstance(previous, HTTPError) and previous.retry_after > 0:
        return previous.retry_after
    shift = min(attempt - 1, 6)
    delay = base * (1 << shift)
    if delay <= 0:
        return 0.0
    return random.uniform(0.0, delay)  # full jitter


def _parse_retry_after(value: str) -> float:
    value = value.strip()
    if not value:
        return 0.0
    if value.isdigit():
        return float(value)
    try:
        timestamp = parsedate_to_datetime(value)
        now = datetime.now(timezone.utc)
        return max((timestamp - now).total_seconds(), 0.0)
    except (ValueError, TypeError):
        return 0.0
