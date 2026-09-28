"""Application factory (mirrors Go httpapi.New + route registration)."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import ValidationError

from ..errors import ApiError, json_response
from ..interview.errors import DomainError, ErrorCode
from ..llm.schema import StrictModel
from ..middleware import Security
from ..session import Memory, Store
from ..streaming import sse_done, sse_event
from .projections import (
    deferred_feedback,
    feedback_focus,
    map_feedback,
    map_profile,
    map_progress,
    map_question,
    map_report,
    map_review,
    map_snapshot,
    web_state,
)

CHAT_SYSTEM_PROMPT = (
    "你是 OfferPilot，一名严谨的 AI Agent / LLM 工程面试教练。基于用户实际提供的问题和材料作答；"
    "区分事实、候选人陈述与推断，不编造简历经历。诊断回答时关注原理、个人职责、量化口径、方案取舍和边界条件，并给出可执行改进。"
)


class ChatInput(StrictModel):
    message: str = ""
    sessionId: str = ""
    model: str = ""


class MatchInput(StrictModel):
    jd: str = ""
    resume: str = ""


class ResumeInput(StrictModel):
    content: str = ""
    images: list[str] = field(default_factory=list)


class CrawlInput(StrictModel):
    url: str = ""


class TTSInput(StrictModel):
    text: str = ""
    voice: str = ""
    format: str = ""


@dataclass
class AppConfig:
    version: str = "dev"
    api_key: str = ""
    require_auth: bool = False
    allowed_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    model_configured: bool = False
    speech_configured: bool = False
    knowledge_entries: int = 0
    knowledge_index: object | None = None
    interview: object | None = None
    chat: object | None = None
    speech: object | None = None
    crawler: object | None = None
    matcher: object | None = None
    resume_diagnostician: object | None = None
    max_json_body_bytes: int = 256 << 10
    max_interview_bytes: int = 2 << 20
    max_audio_body_bytes: int = 25 << 20
    max_resume_diagnosis_bytes: int = 12 << 20
    max_message_chars: int = 20000
    max_tts_text_chars: int = 5000
    max_url_chars: int = 4096


def health_payload(config: AppConfig, status: str) -> dict:
    harness = "ready" if config.model_configured else "not_ready"
    return {
        "status": status,
        "service": "offerpilot-go",
        "version": config.version,
        "live": True,
        "ready": config.model_configured,
        "readiness": harness,
        "harness": harness,
        "modelConfigured": config.model_configured,
        "speechConfigured": config.speech_configured,
        "crawlerConfigured": config.crawler is not None,
        "matcherConfigured": config.matcher is not None,
        "resumeDiagnosticianConfigured": config.resume_diagnostician is not None,
        "knowledgeEntries": config.knowledge_entries,
    }


_INTERVIEW_ERROR_STATUS = {
    ErrorCode.VALIDATION: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.GROUNDING: 422,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.INTERNAL: 500,
}


def raise_interview_error(err: Exception) -> None:
    if isinstance(err, DomainError):
        status = _INTERVIEW_ERROR_STATUS.get(err.code, 500)
        retryable = err.code in (ErrorCode.CONFLICT, ErrorCode.SERVICE_UNAVAILABLE, ErrorCode.INTERNAL)
        raise ApiError(status, err.code, err.message, retryable, err.field)
    raise ApiError(500, "internal", "Interview service failed", True)


async def read_body(request: Request, limit: int) -> bytes:
    body = await request.body()
    if len(body) > limit:
        raise ApiError(413, "body_too_large", f"request body exceeds {limit} bytes", False)
    return body


async def read_strict(request: Request, limit: int, model):
    body = await read_body(request, limit)
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(400, "invalid_json", "Invalid JSON", False)
    try:
        return model.model_validate(data)
    except ValidationError:
        raise ApiError(400, "invalid_json", "Invalid JSON", False)


async def read_lenient(request: Request, limit: int) -> dict:
    body = await read_body(request, limit)
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(400, "invalid_json", "Invalid JSON", False)
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_json", "Invalid JSON", False)
    return data


def create_app(config: AppConfig) -> FastAPI:
    security = Security(config.api_key, config.require_auth, config.allowed_origins)
    sessions = Store()
    memory = Memory(40)

    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError):
        return json_response(exc.payload(), exc.status)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception):
        return json_response(
            {"error": {"code": "internal", "message": "Internal server error", "retryable": True}},
            500,
        )

    app.middleware("http")(security.middleware)

    @app.get("/health")
    async def health():
        return json_response(health_payload(config, "ok"))

    @app.get("/health/live")
    async def health_live():
        return json_response(health_payload(config, "live"))

    @app.get("/health/ready")
    async def health_ready():
        if not config.model_configured:
            return json_response(health_payload(config, "not_ready"), 503)
        return json_response(health_payload(config, "ready"))

    @app.post("/api/session", dependencies=[Depends(security.authenticate)])
    async def create_session():
        session = sessions.create()
        return json_response({"sessionId": session.id})

    # --- chat ---

    @app.post("/api/chat", dependencies=[Depends(security.authenticate)])
    async def handle_chat(request: Request):
        if config.chat is None:
            raise ApiError(503, "chat_unavailable", "Text model is not configured", True)
        input = await read_strict(request, config.max_json_body_bytes, ChatInput)
        input.message = input.message.strip()
        if not input.message:
            raise ApiError(400, "validation", "message is required", False, "message")
        if len(input.message) > config.max_message_chars:
            raise ApiError(413, "message_too_large", f"message exceeds {config.max_message_chars} characters", False, "message")

        active = sessions.get(input.sessionId)
        if active is None:
            active = sessions.create(input.sessionId)
        memory.add(active.id, "user", input.message)
        from ..chat import Message as ChatMessage

        messages = [ChatMessage(role="system", content=CHAT_SYSTEM_PROMPT)]
        for item in memory.messages(active.id):
            if item.role in ("user", "assistant"):
                messages.append(ChatMessage(role=item.role, content=item.content))

        queue: asyncio.Queue = asyncio.Queue()

        async def on_delta(delta):
            if delta.thinking:
                await queue.put(("thinking_delta", delta.thinking))
            if delta.text:
                await queue.put(("text_delta", delta.text))

        async def run_model():
            try:
                usage = await config.chat.stream(input.model, messages, on_delta)
                await queue.put(("done", usage))
            except Exception as err:  # noqa: BLE001
                await queue.put(("error", str(err)))

        task = asyncio.create_task(run_model())

        async def event_stream():
            yield sse_event({"type": "session", "sessionId": active.id})
            assistant: list[str] = []
            while True:
                kind, payload = await queue.get()
                if kind == "text_delta":
                    assistant.append(payload)
                    yield sse_event({"type": "text_delta", "content": payload})
                elif kind == "thinking_delta":
                    yield sse_event({"type": "thinking_delta", "content": payload})
                elif kind == "done":
                    if assistant:
                        memory.add(active.id, "assistant", "".join(assistant))
                    yield sse_event({"type": "done", "usage": {"inputTokens": payload.input_tokens, "outputTokens": payload.output_tokens}})
                    break
                else:
                    yield sse_event({"type": "error", "message": payload})
                    break
            yield sse_done()
            await task

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream; charset=utf-8",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    # --- interview ---

    @app.post("/api/interview", dependencies=[Depends(security.authenticate)])
    async def handle_interview(request: Request):
        if not config.model_configured:
            raise ApiError(503, ErrorCode.SERVICE_UNAVAILABLE, "Interview model is not configured", True)
        if config.interview is None:
            raise ApiError(503, ErrorCode.SERVICE_UNAVAILABLE, "Interview model is not configured", True)
        data = await read_lenient(request, config.max_interview_bytes)
        action = data.get("action")
        from ..interview.types import AnswerRequest, ReportRequest, StartRequest

        if action == "start":
            input = StartRequest.model_validate(data)
            if not input.clientSessionId.strip():
                input.clientSessionId = sessions.create().id
            try:
                output = await config.interview.start(input)
            except DomainError as err:
                raise_interview_error(err)
            return json_response({
                "interviewId": output.interviewId,
                "state": web_state(output.state),
                "profile": map_profile(output.profile),
                "question": map_question(output.question, output.progress.current, output.profile),
                "progress": map_progress(output.progress),
            })
        if action == "answer":
            input = AnswerRequest.model_validate(data)
            try:
                output = await config.interview.answer(input)
            except DomainError as err:
                raise_interview_error(err)
            next_question = None
            if output.nextQuestion is not None:
                next_question = map_question(output.nextQuestion, output.progress.current, None)
            if output.feedback.deferred:
                feedback = deferred_feedback(input.questionId)
            else:
                feedback = map_feedback(
                    input.questionId, output.feedback.assessment, output.feedback.summary,
                    feedback_focus(output.feedback.focus),
                )
            return json_response({
                "interviewId": output.interviewId,
                "state": web_state(output.state),
                "feedback": feedback,
                "nextQuestion": next_question,
                "progress": map_progress(output.progress),
                "reportReady": output.reportReady,
            })
        if action == "report":
            input = ReportRequest.model_validate(data)
            try:
                output = await config.interview.report(input)
            except DomainError as err:
                raise_interview_error(err)
            return json_response(map_report(output.interviewId, output.report))
        raise ApiError(400, "validation", "action must be start, answer, or report", False, "action")

    @app.post("/api/v1/interview", dependencies=[Depends(security.authenticate)])
    async def handle_interview_v1(request: Request):
        return await handle_interview(request)

    @app.get("/api/v1/interviews/{interview_id}", dependencies=[Depends(security.authenticate)])
    async def handle_snapshot(interview_id: str):
        if config.interview is None:
            raise ApiError(503, ErrorCode.SERVICE_UNAVAILABLE, "Interview recovery is not configured", True)
        try:
            snapshot = await config.interview.snapshot(interview_id)
        except DomainError as err:
            raise_interview_error(err)
        return json_response(map_snapshot(snapshot))

    @app.get("/api/v1/interviews/{interview_id}/review", dependencies=[Depends(security.authenticate)])
    async def handle_review(interview_id: str):
        if config.interview is None:
            raise ApiError(503, ErrorCode.SERVICE_UNAVAILABLE, "Interview review is not configured", True)
        try:
            review = await config.interview.review(interview_id)
        except DomainError as err:
            raise_interview_error(err)
        return json_response(map_review(review))

    @app.get("/api/v1/interviews/{interview_id}/events", dependencies=[Depends(security.authenticate)])
    async def handle_events(interview_id: str, after: int = 0, limit: int = 100):
        if after < 0:
            raise ApiError(400, "validation", "after must be a non-negative integer", False, "after")
        if limit < 1 or limit > 1000:
            raise ApiError(400, "validation", "limit must be between 1 and 1000", False, "limit")
        if config.interview is None:
            raise ApiError(503, ErrorCode.SERVICE_UNAVAILABLE, "Interview recovery is not configured", True)
        try:
            page = await config.interview.session_events(interview_id, after, limit)
        except DomainError as err:
            raise_interview_error(err)
        return json_response(page.model_dump(mode="json"))

    # --- speech ---

    @app.post("/api/transcribe", dependencies=[Depends(security.authenticate)])
    async def handle_transcribe(request: Request):
        if config.speech is None:
            return json_response({"error": "语音识别服务尚未配置，请联系管理员", "retryable": False}, 503)
        audio = await read_body(request, config.max_audio_body_bytes)
        if not audio:
            return json_response({"error": "audio body is required"}, 400)
        from ..speech import TranscribeInput, classify_transcription_error

        try:
            text = await config.speech.transcribe(TranscribeInput(
                audio=audio,
                file_name=request.headers.get("x-file-name", ""),
                content_type=request.headers.get("content-type", ""),
            ))
        except Exception as err:  # noqa: BLE001
            failure = classify_transcription_error(err)
            return json_response({"error": failure.message, "retryable": failure.retryable}, failure.status)
        return json_response({"text": text})

    @app.post("/api/tts", dependencies=[Depends(security.authenticate)])
    async def handle_tts(request: Request):
        if config.speech is None:
            return json_response({"error": "Speech model is not configured"}, 503)
        input = await read_strict(request, config.max_json_body_bytes, TTSInput)
        if not input.text.strip():
            return json_response({"error": "text is required"}, 400)
        if len(input.text) > config.max_tts_text_chars:
            return json_response({"error": f"text exceeds {config.max_tts_text_chars} characters"}, 413)
        from ..speech import SynthesizeInput

        try:
            audio = await config.speech.synthesize(SynthesizeInput(text=input.text, voice=input.voice, format=input.format))
        except Exception as err:  # noqa: BLE001
            return json_response({"error": str(err)}, 502)
        return Response(content=audio.data, media_type=audio.content_type, headers={"Cache-Control": "no-store"})

    # --- crawler / matcher / resume ---

    @app.post("/api/crawl", dependencies=[Depends(security.authenticate)])
    async def handle_crawl(request: Request):
        if config.crawler is None:
            raise ApiError(503, "crawler_unavailable", "Web crawler Agent is not configured", True)
        input = await read_strict(request, config.max_json_body_bytes, CrawlInput)
        input.url = input.url.strip()
        if not input.url:
            raise ApiError(400, "validation_error", "url is required", False, "url")
        if len(input.url) > config.max_url_chars:
            raise ApiError(413, "url_too_large", "url is too long", False, "url")
        from ..webcrawler import (
            BlockedURLError,
            CrawlTimeoutError,
            InvalidURLError,
            NoContentError,
            Request as CrawlRequest,
            ResponseLargeError,
        )

        try:
            result = await config.crawler.crawl(CrawlRequest(url=input.url))
        except InvalidURLError:
            raise ApiError(400, "invalid_url", "URL must be a public HTTP or HTTPS address", False, "url")
        except BlockedURLError:
            raise ApiError(400, "url_not_allowed", "Private, loopback, link-local, and reserved URLs are not allowed", False, "url")
        except ResponseLargeError:
            raise ApiError(413, "page_too_large", "Web page exceeds the crawler response limit", False)
        except NoContentError:
            raise ApiError(422, "page_content_missing", "No useful page content was found", False)
        except CrawlTimeoutError:
            raise ApiError(408, "crawl_timeout", "Web crawl timed out", True)
        except asyncio.CancelledError:
            raise ApiError(408, "crawl_canceled", "Web crawl was canceled", True)
        except Exception:  # noqa: BLE001
            raise ApiError(502, "crawl_failed", "Could not fetch the requested web page", True)
        return json_response(result.model_dump(mode="json"))

    @app.post("/api/v1/crawl", dependencies=[Depends(security.authenticate)])
    async def handle_crawl_v1(request: Request):
        return await handle_crawl(request)

    @app.post("/api/match", dependencies=[Depends(security.authenticate)])
    async def handle_match(request: Request):
        if config.matcher is None:
            raise ApiError(503, "matcher_unavailable", "Resume matcher Agent is not configured", True)
        input = await read_strict(request, config.max_interview_bytes, MatchInput)
        input.jd = input.jd.strip()
        input.resume = input.resume.strip()
        if not input.jd or not input.resume:
            raise ApiError(400, "validation_error", "jd and resume are required", False)
        from ..jobmatch import Request as JobMatchRequest

        try:
            result = await config.matcher.match(JobMatchRequest(jd=input.jd, resume=input.resume))
        except Exception:  # noqa: BLE001
            raise ApiError(503, "matching_failed", "Semantic matching is temporarily unavailable", True)
        return json_response(result.model_dump(mode="json"))

    @app.post("/api/v1/match", dependencies=[Depends(security.authenticate)])
    async def handle_match_v1(request: Request):
        return await handle_match(request)

    @app.post("/api/resume/diagnose", dependencies=[Depends(security.authenticate)])
    async def handle_resume_diagnosis(request: Request):
        if config.resume_diagnostician is None:
            raise ApiError(503, "resume_diagnostician_unavailable", "Resume diagnostician Agent is not configured", True)
        input = await read_strict(request, config.max_resume_diagnosis_bytes, ResumeInput)
        input.content = input.content.strip()
        if not input.content:
            raise ApiError(400, "validation_error", "resume content is required", False, "content")
        if len(input.images) > 3:
            raise ApiError(400, "too_many_images", "at most three resume page images are allowed", False, "images")
        for image in input.images:
            if len(image) > (3 << 20) or not (image.startswith("data:image/jpeg;base64,") or image.startswith("data:image/png;base64,")):
                raise ApiError(400, "invalid_image", "resume images must be bounded JPEG or PNG data URLs", False, "images")
        from ..resumediagnosis import Request as DiagnoseRequest

        try:
            result = await config.resume_diagnostician.diagnose(DiagnoseRequest(content=input.content, images=input.images))
        except Exception:  # noqa: BLE001
            raise ApiError(503, "resume_diagnosis_failed", "Multimodal resume diagnosis is temporarily unavailable", True)
        return json_response(result.model_dump(mode="json"))

    @app.post("/api/v1/resume/diagnose", dependencies=[Depends(security.authenticate)])
    async def handle_resume_diagnosis_v1(request: Request):
        return await handle_resume_diagnosis(request)

    return app
