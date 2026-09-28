"""P3 speech (MiMo ASR/TTS) tests."""
import base64
import json

import httpx
import pytest

from app.speech import (
    AudioRequiredError,
    Client,
    Config,
    EmptyTranscriptError,
    InvalidResponseError,
    ProviderRejectedError,
    ProviderUnavailableError,
    SynthesizeInput,
    TranscriptionFailure,
    TranscribeInput,
    UnsupportedAudioError,
    classify_transcription_error,
)


async def _unreachable(request):
    raise AssertionError("unexpected HTTP call")


def make_client(handler=_unreachable) -> Client:
    return Client(Config(api_key="k", base_url="https://example.com/v1"), httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_classify_transcription_error():
    assert classify_transcription_error(AudioRequiredError()) == TranscriptionFailure(400, "没有收到录音，请重新录制")
    assert classify_transcription_error(UnsupportedAudioError()) == TranscriptionFailure(415, "当前录音格式无法识别，请重新录制")
    assert classify_transcription_error(EmptyTranscriptError()) == TranscriptionFailure(422, "未识别到有效语音，请重新录制")
    assert classify_transcription_error(ProviderRejectedError(401)) == TranscriptionFailure(503, "语音识别服务配置异常，请联系管理员")
    assert classify_transcription_error(ProviderRejectedError(400)) == TranscriptionFailure(422, "录音无法被语音服务处理，请重新录制")
    assert classify_transcription_error(ProviderUnavailableError()).retryable is True
    assert classify_transcription_error(InvalidResponseError()) == TranscriptionFailure(502, "语音识别服务响应异常，请重试", True)


async def test_transcribe_empty_audio():
    with pytest.raises(AudioRequiredError):
        await make_client().transcribe(TranscribeInput(audio=b""))


async def test_transcribe_unsupported_format():
    with pytest.raises(UnsupportedAudioError):
        await make_client().transcribe(TranscribeInput(audio=b"x", content_type="audio/ogg"))


async def test_transcribe_happy_path():
    async def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "mimo-v2.5-asr"
        assert body["asr_options"] == {"language": "auto"}
        part = body["messages"][0]["content"][0]
        assert part["type"] == "input_audio"
        assert part["input_audio"]["data"].startswith("data:audio/wav;base64,")
        return httpx.Response(200, json={"text": " 你好世界 "})

    text = await make_client(handler).transcribe(TranscribeInput(audio=b"abc", content_type="audio/wav"))
    assert text == "你好世界"


async def test_transcribe_choices_fallback():
    async def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": [{"type": "text", "text": "你好"}]}}]})

    text = await make_client(handler).transcribe(TranscribeInput(audio=b"x", content_type="audio/mpeg"))
    assert text == "你好"


async def test_transcribe_empty_transcript():
    async def handler(request):
        return httpx.Response(200, json={})

    with pytest.raises(EmptyTranscriptError):
        await make_client(handler).transcribe(TranscribeInput(audio=b"x", content_type="audio/wav"))


async def test_transcribe_retries_then_succeeds():
    calls = []

    async def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, content=b"{}")
        return httpx.Response(200, json={"text": "ok"})

    text = await make_client(handler).transcribe(TranscribeInput(audio=b"x", content_type="audio/wav"))
    assert text == "ok"
    assert len(calls) == 3


async def test_transcribe_no_retry_on_4xx():
    calls = []

    async def handler(request):
        calls.append(1)
        return httpx.Response(400, content=b"{}")

    with pytest.raises(ProviderRejectedError):
        await make_client(handler).transcribe(TranscribeInput(audio=b"x", content_type="audio/wav"))
    assert len(calls) == 1


async def test_synthesize():
    async def handler(request):
        body = json.loads(request.content)
        assert body["audio"] == {"format": "mp3", "voice": "mimo_default"}
        return httpx.Response(200, json={"choices": [{"message": {"audio": {"data": base64.b64encode(b"RAW").decode()}}}]})

    audio = await make_client(handler).synthesize(SynthesizeInput(text="你好", format="mp3"))
    assert audio.data == b"RAW"
    assert audio.content_type == "audio/mpeg"


async def test_synthesize_empty_text():
    with pytest.raises(ValueError):
        await make_client().synthesize(SynthesizeInput(text="  "))
