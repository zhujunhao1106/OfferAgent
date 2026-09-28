"""P4d interview service idempotency + recovery projection tests."""
import json
from datetime import datetime, timezone

import pytest

from app.interview.errors import DomainError, ErrorCode
from app.interview.memory_store import MemoryStore
from app.interview.persistence import SessionEventSpec
from app.interview.service import Service
from app.interview.sources import evidence_from_anchor
from app.interview.sqlite_store import SQLiteStore
from app.interview.types import (
    Action,
    AnswerPayload,
    AnswerRequest,
    Assessment,
    Difficulty,
    Focus,
    FeedbackMode,
    InputMode,
    InterviewConfig,
    KnowledgeDocument,
    MaterialInput,
    MaterialsInput,
    QuestionDraft,
    ReportDraft,
    ReportRequest,
    StartRequest,
)

NOW = datetime.now(timezone.utc)


class GroundedAgent:
    def __init__(self, assess_fn=None):
        self.question_calls = 0
        self.assess_calls = 0
        self.report_calls = 0
        self.assess_fn = assess_fn

    async def generate_question(self, request):
        self.question_calls += 1
        return QuestionDraft(
            text=f"grounded question {self.question_calls}",
            evidenceRefs=[evidence_from_anchor(request.anchors[0])],
        )

    async def assess_answer(self, request):
        self.assess_calls += 1
        if self.assess_fn is not None:
            return self.assess_fn(request, self.assess_calls)
        assessment = Assessment(correctness=3, depth=3, specificity=3, ownership=3, metrics=3, tradeoffs=3)
        if not assessment.evidenceRefs and request.anchors:
            assessment.evidenceRefs = [evidence_from_anchor(request.anchors[0])]
        return assessment

    async def generate_report(self, request):
        self.report_calls += 1
        return ReportDraft(summary="report", evidenceRefs=[evidence_from_anchor(request.anchors[0])])


class SequenceIDs:
    def __init__(self):
        self.next = 0

    def new_id(self, prefix):
        self.next += 1
        return prefix + "-" + chr(ord("a") + self.next - 1)


class FixedClock:
    def __init__(self):
        self.value = NOW

    def now(self):
        return self.value


class StaticRetriever:
    def __init__(self, documents):
        self._documents = documents

    def retrieve(self, query):
        return list(self._documents)


def new_test_service(agent, store):
    return Service(agent=agent, store=store, clock=FixedClock(), ids=SequenceIDs())


def standard_materials():
    return MaterialsInput(
        jd=MaterialInput(text="高级 Go 工程师\n负责设计高并发支付平台\n要求理解 Go 并发、Redis 和 Kafka"),
        resume=MaterialInput(text="后端工程师\n项目 OfferPilot：我负责设计自适应面试 Agent\n将响应延迟从 500ms 降至 120ms\n负责消息队列削峰与故障恢复"),
    )


def start_request(question_count, focus, materials):
    return StartRequest(
        action=Action.START, clientSessionId="client-1", model="test-model",
        config=InterviewConfig(
            focus=focus, difficulty=Difficulty.MEDIUM, questionCount=question_count,
            language="zh-CN", feedbackMode=FeedbackMode.IMMEDIATE,
        ),
        materials=materials,
    )


def answer_request(source, text):
    question = source.question if hasattr(source, "question") else source.nextQuestion
    return AnswerRequest(
        action=Action.ANSWER, interviewId=source.interviewId, questionId=question.id,
        clientAnswerId="answer-" + question.id,
        answer=AnswerPayload(text=text, inputMode=InputMode.TEXT, durationMs=1000),
    )


async def test_full_flow_with_memory_store():
    agent = GroundedAgent()
    service = new_test_service(agent, MemoryStore())
    started = await service.start(start_request(2, Focus.MIXED, standard_materials()))
    assert started.interviewId == "interview-a"
    assert started.progress.total == 2

    first = await service.answer(answer_request(started, "first answer"))
    assert first.state.value == "awaiting_answer"
    assert first.nextQuestion is not None

    second = await service.answer(answer_request(first, "second answer"))
    assert second.state.value == "completed"
    assert second.reportReady is True

    report = await service.report(ReportRequest(action=Action.REPORT, interviewId=started.interviewId))
    assert report.report.overallScore == 60
    assert report.report.audit.clientSessionId == "client-1"


async def test_memory_answer_replay_and_conflicts():
    agent = GroundedAgent()
    service = new_test_service(agent, MemoryStore())
    started = await service.start(start_request(2, Focus.MIXED, standard_materials()))
    request = answer_request(started, "stable answer")
    request.clientAnswerId = "client-answer-stable"

    first = await service.answer(request)
    replayed = await service.answer(request)
    assert replayed == first

    changed = request.model_copy(deep=True)
    changed.answer.text = "different payload"
    with pytest.raises(DomainError) as exc:
        await service.answer(changed)
    assert exc.value.code == ErrorCode.CONFLICT

    changed_key = request.model_copy(deep=True)
    changed_key.clientAnswerId = "client-answer-other"
    with pytest.raises(DomainError) as exc:
        await service.answer(changed_key)
    assert exc.value.code == ErrorCode.CONFLICT
    assert agent.assess_calls == 1


async def test_sqlite_answer_replay_conflicts_and_events():
    store = await SQLiteStore.open(":memory:")
    try:
        agent = GroundedAgent()
        service = new_test_service(agent, store)
        materials = standard_materials()
        materials.jd.text += "\nSECRET_JD_IDEMPOTENCY"
        materials.resume.text += "\nSECRET_RESUME_IDEMPOTENCY"
        started = await service.start(start_request(2, Focus.MIXED, materials))
        request = answer_request(started, "SECRET_ANSWER_IDEMPOTENCY")
        request.clientAnswerId = "client-answer-stable"

        first = await service.answer(request)
        replayed = await service.answer(request)
        assert replayed == first

        changed = request.model_copy(deep=True)
        changed.answer.text = "different payload"
        with pytest.raises(DomainError) as exc:
            await service.answer(changed)
        assert exc.value.code == ErrorCode.CONFLICT

        changed_key = request.model_copy(deep=True)
        changed_key.clientAnswerId = "client-answer-other"
        with pytest.raises(DomainError) as exc:
            await service.answer(changed_key)
        assert exc.value.code == ErrorCode.CONFLICT

        assert agent.assess_calls == 1
        assert agent.question_calls == 2
        loaded = await store.load(started.interviewId)
        assert len(loaded.answers) == 1
        assert loaded.version == 2

        events = await store.list_session_events(started.interviewId, 0, 20)
        assert [e.type for e in events] == ["answer.started", "answer.committed"]
        encoded = json.dumps([e.payload for e in events])
        for secret in ("SECRET_ANSWER_IDEMPOTENCY", "SECRET_JD_IDEMPOTENCY", "SECRET_RESUME_IDEMPOTENCY"):
            assert secret not in encoded
    finally:
        await store.close()


async def test_failed_answer_retries_without_partial_commit():
    store = await SQLiteStore.open(":memory:")
    try:
        agent = GroundedAgent(assess_fn=lambda request, call: (
            (_ for _ in ()).throw(RuntimeError("SECRET_PROVIDER_FAILURE"))
            if call == 1
            else Assessment(correctness=3, depth=3, specificity=3, ownership=3, metrics=3, tradeoffs=3,
                            evidenceRefs=[evidence_from_anchor(request.anchors[0])])
        ))
        service = new_test_service(agent, store)
        started = await service.start(start_request(2, Focus.MIXED, standard_materials()))
        request = answer_request(started, "answer survives retry")
        request.clientAnswerId = "client-answer-retry"

        with pytest.raises(DomainError) as exc:
            await service.answer(request)
        assert exc.value.code == ErrorCode.SERVICE_UNAVAILABLE

        loaded = await store.load(started.interviewId)
        assert len(loaded.answers) == 0
        assert loaded.version == 1

        second = await service.answer(request)
        replayed = await service.answer(request)
        assert replayed == second
        assert agent.assess_calls == 2

        events = await store.list_session_events(started.interviewId, 0, 20)
        assert [e.type for e in events] == ["answer.started", "answer.failed", "answer.started", "answer.committed"]
        encoded = json.dumps([e.payload for e in events])
        assert "SECRET_PROVIDER_FAILURE" not in encoded
    finally:
        await store.close()


async def test_snapshot_projects_public_question_without_private_reference():
    private = "SNAPSHOT_PRIVATE_REFERENCE_6D2A"
    documents = [KnowledgeDocument(
        id="snapshot", title="Snapshot knowledge",
        content="问题：如何设计幂等回答？\n参考内容：" + private,
    )]
    agent = GroundedAgent(assess_fn=lambda request, call: Assessment(
        correctness=4, depth=4, specificity=3, ownership=3, metrics=2, tradeoffs=4,
        strengths=["说明了幂等键"], gaps=["需要补充冲突语义"],
        evidenceRefs=[evidence_from_anchor(request.anchors[0])],
    ))
    service = Service(
        agent=agent, retriever=StaticRetriever(documents), store=MemoryStore(),
        clock=FixedClock(), ids=SequenceIDs(),
    )
    started = await service.start(start_request(2, Focus.KNOWLEDGE, MaterialsInput()))
    answered = await service.answer(answer_request(started, "使用客户端幂等键与请求哈希。"))

    snapshot = await service.snapshot(started.interviewId)
    assert snapshot.currentQuestion is not None
    assert snapshot.currentQuestion.id == answered.nextQuestion.id
    assert len(snapshot.turns) == 1
    assert snapshot.turns[0].answer.text != ""
    assert snapshot.turns[0].feedback.summary != ""
    encoded = json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False)
    assert private not in encoded
    assert "参考内容" not in encoded


async def test_session_events_returns_metadata_without_payload():
    store = await SQLiteStore.open(":memory:")
    try:
        agent = GroundedAgent()
        service = new_test_service(agent, store)
        started = await service.start(start_request(1, Focus.PROJECTS, standard_materials()))
        await store.append_session_event(SessionEventSpec(
            event_id="event-safe", session_id=started.interviewId, command_id="command-safe",
            type="answer.accepted", payload='{"private":"must-not-be-returned"}',
        ))
        page = await service.session_events(started.interviewId, 0, 10)
        assert len(page.events) == 1
        assert page.nextSequence == 1
        assert "must-not-be-returned" not in json.dumps(page.model_dump(mode="json"), ensure_ascii=False)
    finally:
        await store.close()


async def test_review_projection_exposes_answered_knowledge_reference():
    private = "REVIEW_PRIVATE_ANSWER_4F2B"
    documents = [KnowledgeDocument(
        id="kb", title="T", content="问题：如何设计幂等回答？\n参考内容：" + private,
    )]
    agent = GroundedAgent(assess_fn=lambda request, call: Assessment(
        correctness=4, depth=4, specificity=3, ownership=3, metrics=2, tradeoffs=4,
        evidenceRefs=[evidence_from_anchor(request.anchors[0])],
    ))
    service = Service(
        agent=agent, retriever=StaticRetriever(documents), store=MemoryStore(),
        clock=FixedClock(), ids=SequenceIDs(),
    )
    started = await service.start(start_request(2, Focus.KNOWLEDGE, MaterialsInput()))
    await service.answer(answer_request(started, "使用客户端幂等键与请求哈希。"))
    review = await service.review(started.interviewId)
    assert review.schemaVersion == "1.0.0"
    assert review.turns[0].references[0].answer == private
