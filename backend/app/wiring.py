"""Assemble the full application stack from Settings (mirrors Go cmd/offerpilot-api/main.go)."""
from __future__ import annotations

import logging
from pathlib import Path

from .api import AppConfig
from .chat import Client as ChatClient
from .chat import Config as ChatConfig
from .harness import Runtime
from .harness.interview_agent import InterviewAgent, InterviewAgentOptions
from .harness.trace import TRACE_ERROR
from .interview import Service
from .interview.sqlite_store import SQLiteStore
from .jobmatch import Matcher
from .knowledge import load as load_knowledge
from .knowledge.retriever import InterviewRetriever
from .llm import Client as LLMClient
from .llm import Config as LLMConfig
from .resumediagnosis import Diagnostician
from .settings import Settings
from .speech import Client as SpeechClient
from .speech import Config as SpeechConfig
from .webcrawler import CrawlerAgent, Fetcher

logger = logging.getLogger("offerpilot")


async def assemble(settings: Settings) -> AppConfig:
    knowledge_dir = resolve_knowledge_directory(settings.knowledge_dir)
    index = load_knowledge(str(knowledge_dir))
    retriever = InterviewRetriever(index, settings.interview_knowledge_limit)

    store = await SQLiteStore.open(str(resolve_data_path(settings.db_path)))

    app_config = AppConfig(
        version=settings.version,
        api_key=settings.server_api_key,
        require_auth=settings.require_auth,
        allowed_origins=settings.allowed_origins,
        model_configured=settings.model_configured,
        speech_configured=settings.speech_configured,
        knowledge_entries=index.len(),
        max_json_body_bytes=settings.max_json_body_bytes,
        max_interview_bytes=settings.max_interview_bytes,
        max_audio_body_bytes=settings.max_audio_body_bytes,
        max_resume_diagnosis_bytes=settings.max_resume_diagnosis_bytes,
        max_message_chars=settings.max_message_chars,
        max_tts_text_chars=settings.max_tts_text_chars,
        max_url_chars=settings.max_url_chars,
    )
    app_config.store = store

    interview_agent = None
    if settings.model_configured:
        model_client = LLMClient(LLMConfig(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            timeout=settings.openai_timeout,
            max_retries=settings.openai_max_retries,
            retry_base_wait=settings.openai_retry_base_wait,
            max_tokens=settings.openai_max_tokens,
        ))
        runtime = Runtime(
            model_client,
            max_concurrent=settings.harness_max_concurrent,
            trace_capacity=settings.harness_trace_capacity,
            trace_sink=_harness_trace_sink,
        )
        interview_agent = InterviewAgent(runtime, InterviewAgentOptions(
            interviewer_timeout=settings.interviewer_timeout,
            assessor_timeout=settings.assessor_timeout,
            reporter_timeout=settings.reporter_timeout,
            planner_timeout=settings.planner_timeout,
        ))
        app_config.chat = ChatClient(ChatConfig(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.chat_model,
            timeout=settings.chat_timeout,
            max_tokens=settings.openai_max_tokens,
        ))
        app_config.crawler = CrawlerAgent(
            runtime,
            Fetcher(
                request_timeout=settings.crawler_request_timeout,
                max_response_bytes=settings.crawler_max_response_bytes,
                max_redirects=settings.crawler_max_redirects,
                allow_benchmark_tunnel=settings.crawler_allow_benchmark_tunnel,
            ),
            max_fallback_iterations=settings.crawler_fallback_max_iterations,
            fallback_timeout=settings.crawler_fallback_timeout,
            decision_timeout=settings.crawler_decision_timeout,
        )
        app_config.matcher = Matcher(runtime, settings.matcher_timeout)
        app_config.resume_diagnostician = Diagnostician(runtime, settings.resume_diagnostician_timeout)
    else:
        logger.warning(
            "text model is not configured; interview, chat, match, resume diagnosis "
            "and crawler fallback are unavailable (required_env=OPENAI_API_KEY)"
        )

    app_config.interview = Service(
        agent=interview_agent,
        retriever=retriever,
        store=store,
    )

    if settings.speech_configured:
        app_config.speech = SpeechClient(SpeechConfig(
            api_key=settings.mimo_api_key,
            base_url=settings.mimo_base_url,
            asr_model=settings.mimo_asr_model,
            tts_model=settings.mimo_tts_model,
            tts_voice=settings.mimo_tts_voice,
            asr_language=settings.mimo_asr_language,
            timeout=settings.mimo_timeout,
        ))

    return app_config


def _harness_trace_sink(event) -> None:
    if event.type != TRACE_ERROR:
        return
    logger.warning(
        "harness call failed trace_id=%s agent=%s tool=%s duration_ms=%d error=%s",
        event.trace_id,
        event.agent_id,
        event.tool_name,
        int(event.duration * 1000),
        event.error,
    )


def resolve_knowledge_directory(configured: str) -> Path:
    path = Path(configured)
    if path.is_absolute():
        if path.is_dir():
            return path
        raise ValueError(f"{configured} does not exist or is not a directory")
    cwd = Path.cwd()
    for candidate in (cwd / configured, cwd.parent / configured):
        if candidate.is_dir():
            return candidate.resolve()
    raise ValueError(f"{configured} not found from {cwd} or its parent")


def resolve_data_path(configured: str) -> Path:
    configured = configured.strip()
    if not configured:
        raise ValueError("database path is empty")
    path = Path(configured)
    if path.is_absolute():
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    cwd = Path.cwd()
    base = cwd.parent if cwd.name.lower() == "backend" else cwd
    resolved = (base / configured).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved
