"""MiMo ASR/TTS client mirroring Go speech/mimo.go."""
from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass

import httpx

DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_ASR_MODEL = "mimo-v2.5-asr"
DEFAULT_TTS_MODEL = "mimo-v2.5-tts"
DEFAULT_VOICE = "mimo_default"
MAX_RESPONSE_SIZE = 32 << 20
ASR_MAX_ATTEMPTS = 3
ASR_RETRY_DELAY = 0.1


class SpeechError(Exception):
    pass


class AudioRequiredError(SpeechError):
    pass


class UnsupportedAudioError(SpeechError):
    pass


class EmptyTranscriptError(SpeechError):
    pass


class ProviderRejectedError(SpeechError):
    def __init__(self, status: int):
        self.status = status


class ProviderUnavailableError(SpeechError):
    pass


class InvalidResponseError(SpeechError):
    pass


@dataclass
class TranscriptionFailure:
    status: int
    message: str
    retryable: bool = False


def classify_transcription_error(err: Exception) -> TranscriptionFailure:
    if isinstance(err, AudioRequiredError):
        return TranscriptionFailure(400, "没有收到录音，请重新录制")
    if isinstance(err, UnsupportedAudioError):
        return TranscriptionFailure(415, "当前录音格式无法识别，请重新录制")
    if isinstance(err, EmptyTranscriptError):
        return TranscriptionFailure(422, "未识别到有效语音，请重新录制")
    if isinstance(err, ProviderRejectedError):
        if err.status in (401, 403):
            return TranscriptionFailure(503, "语音识别服务配置异常，请联系管理员")
        return TranscriptionFailure(422, "录音无法被语音服务处理，请重新录制")
    if isinstance(err, ProviderUnavailableError):
        return TranscriptionFailure(503, "语音识别服务暂时不可用，请重试", retryable=True)
    if isinstance(err, InvalidResponseError):
        return TranscriptionFailure(502, "语音识别服务响应异常，请重试", retryable=True)
    return TranscriptionFailure(502, "语音识别服务暂时不可用，请重试", retryable=True)


@dataclass
class Config:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    asr_model: str = DEFAULT_ASR_MODEL
    tts_model: str = DEFAULT_TTS_MODEL
    tts_voice: str = DEFAULT_VOICE
    asr_language: str = "auto"
    timeout: float = 60.0


@dataclass
class TranscribeInput:
    audio: bytes
    file_name: str = ""
    content_type: str = ""


@dataclass
class SynthesizeInput:
    text: str
    voice: str = ""
    format: str = ""


@dataclass
class Audio:
    data: bytes
    content_type: str


class Client:
    def __init__(self, config: Config, http_client: httpx.AsyncClient | None = None):
        config.api_key = config.api_key.strip()
        if not config.api_key:
            raise ValueError("speech: MIMO_API_KEY is required")
        config.base_url = config.base_url.strip().rstrip("/") or DEFAULT_BASE_URL
        config.asr_model = config.asr_model.strip() or DEFAULT_ASR_MODEL
        config.tts_model = config.tts_model.strip() or DEFAULT_TTS_MODEL
        config.tts_voice = config.tts_voice.strip() or DEFAULT_VOICE
        config.asr_language = config.asr_language.strip() or "auto"
        if config.timeout <= 0:
            config.timeout = 60.0
        self._config = config
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(self._config.timeout))
        self._retry_attempts = ASR_MAX_ATTEMPTS
        self._retry_delay = ASR_RETRY_DELAY

    async def transcribe(self, input: TranscribeInput) -> str:
        if not input.audio:
            raise AudioRequiredError()
        mime = _normalized_audio_mime(input.content_type, input.file_name)
        if mime is None:
            raise UnsupportedAudioError()
        payload = {
            "model": self._config.asr_model,
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "input_audio",
                    "input_audio": {"data": "data:" + mime + ";base64," + base64.b64encode(input.audio).decode()},
                }],
            }],
            "asr_options": {"language": self._config.asr_language},
        }
        result = await self._post_with_retry(payload)
        text = (result.get("text") or "").strip()
        choices = result.get("choices") or []
        if not text and choices:
            text = _extract_text((choices[0].get("message") or {}).get("content"))
        if not text:
            raise EmptyTranscriptError()
        return text

    async def synthesize(self, input: SynthesizeInput) -> Audio:
        if not input.text.strip():
            raise ValueError("speech: text is required")
        fmt = input.format.strip().lower() or "wav"
        if fmt not in ("wav", "mp3"):
            raise ValueError("speech: TTS format must be wav or mp3")
        voice = input.voice.strip() or self._config.tts_voice
        payload = {
            "model": self._config.tts_model,
            "messages": [
                {"role": "user", "content": "用自然、清晰、适合中文面试反馈的语气朗读。"},
                {"role": "assistant", "content": input.text},
            ],
            "audio": {"format": fmt, "voice": voice},
        }
        result = await self._post(payload)
        choices = result.get("choices") or []
        audio_data = ""
        if choices:
            audio_data = ((choices[0].get("message") or {}).get("audio") or {}).get("data") or ""
        if not audio_data.strip():
            raise ValueError("speech: MiMo TTS returned empty audio")
        data = base64.b64decode(audio_data)
        content_type = "audio/mpeg" if fmt == "mp3" else "audio/wav"
        return Audio(data=data, content_type=content_type)

    async def _post(self, payload: dict) -> dict:
        _, data, err = await self._post_once(json.dumps(payload, ensure_ascii=False).encode())
        if err is not None:
            raise err
        return data

    async def _post_with_retry(self, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode()
        attempts = max(self._retry_attempts, 1)
        delay = max(self._retry_delay, 0.0)
        for attempt in range(1, attempts + 1):
            retryable, data, err = await self._post_once(body)
            if err is None:
                return data
            if not retryable or attempt == attempts:
                raise err
            await asyncio.sleep(delay * (1 << (attempt - 1)))
        raise ProviderUnavailableError()

    async def _post_once(self, body: bytes) -> tuple[bool, dict | None, Exception | None]:
        headers = {
            "api-key": self._config.api_key,
            "Authorization": "Bearer " + self._config.api_key,
            "Content-Type": "application/json",
        }
        try:
            async with asyncio.timeout(self._config.timeout):
                resp = await self._http.post(self._config.base_url + "/chat/completions", headers=headers, content=body)
        except Exception as err:
            return (_is_retryable_transport_error(err), None, ProviderUnavailableError())
        data = resp.content[: MAX_RESPONSE_SIZE + 1]
        if len(data) > MAX_RESPONSE_SIZE:
            return (False, None, InvalidResponseError())
        if resp.status_code < 200 or resp.status_code >= 300:
            if resp.status_code == 408 or resp.status_code == 429 or resp.status_code >= 500:
                return (True, None, ProviderUnavailableError())
            return (False, None, ProviderRejectedError(resp.status_code))
        try:
            return (False, json.loads(data), None)
        except json.JSONDecodeError:
            return (False, None, InvalidResponseError())


def _normalized_audio_mime(content_type: str, file_name: str) -> str | None:
    mime = content_type.split(";")[0].strip().lower()
    name = file_name.strip().lower()
    if mime in ("audio/wav", "audio/x-wav") or name.endswith(".wav"):
        return "audio/wav"
    if mime in ("audio/mpeg", "audio/mp3") or name.endswith(".mp3"):
        return "audio/mpeg"
    return None


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict)).strip()
    return ""


def _is_retryable_transport_error(err: Exception) -> bool:
    if isinstance(err, TimeoutError):
        return True
    if isinstance(err, httpx.TransportError):
        return True
    message = str(err).lower()
    return any(
        marker in message
        for marker in ("connection reset", "forcibly closed", "broken pipe", "server closed idle connection", "server sent goaway")
    )
