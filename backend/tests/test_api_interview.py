"""BFF-facing interview happy-path smoke: start/answer/report through the API."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api import AppConfig, create_app
from app.interview import MemoryStore, Service
from app.interview.sources import evidence_from_anchor
from app.interview.types import (
    Assessment,
    QuestionDraft,
    ReportDraft,
)

NOW = datetime.now(timezone.utc)


class _FakeAgent:
    def __init__(self):
        self.question_calls = 0

    async def generate_question(self, request):
        self.question_calls += 1
        return QuestionDraft(
            text=f"请介绍你在高并发系统上的架构取舍（第{self.question_calls}问）。",
            evidenceRefs=[evidence_from_anchor(request.anchors[0])],
        )

    async def assess_answer(self, request):
        assessment = Assessment(correctness=4, depth=4, specificity=4, ownership=4, metrics=4, tradeoffs=4,
                                strengths=["结构清晰"], gaps=["缺少量化指标"])
        if request.anchors:
            assessment.evidenceRefs = [evidence_from_anchor(request.anchors[0])]
        return assessment

    async def generate_report(self, request):
        return ReportDraft(summary="整体表现良好。", evidenceRefs=[evidence_from_anchor(request.anchors[0])])


class _IDs:
    def __init__(self):
        self.next = 0

    def new_id(self, prefix):
        self.next += 1
        return f"{prefix}-{self.next}"


class _Clock:
    def now(self):
        return NOW


def make_client():
    service = Service(agent=_FakeAgent(), store=MemoryStore(), ids=_IDs(), clock=_Clock())
    app = create_app(AppConfig(model_configured=True, version="0.4.1", knowledge_entries=486, interview=service))
    return TestClient(app, raise_server_exceptions=False)


def test_start_answer_report_flow():
    client = make_client()
    start = client.post("/api/interview", json={
        "action": "start", "clientSessionId": "client-1",
        "config": {"questionCount": 2, "focus": "mixed", "language": "zh-CN", "feedbackMode": "immediate"},
        "materials": {"jd": "高级 Go 工程师\n要求熟悉 Go 并发与 Redis"},
    })
    assert start.status_code == 200, start.text
    body = start.json()
    assert body["interviewId"]
    assert body["state"] == "questioning"
    assert body["progress"]["target"] == 2
    assert body["question"]["kind"] == "opening"
    assert body["question"]["maxDepth"] == 2
    assert body["profile"]["topics"]

    interview_id = body["interviewId"]
    question_id = body["question"]["id"]

    first = client.post("/api/interview", json={
        "action": "answer", "interviewId": interview_id, "questionId": question_id,
        "clientAnswerId": "answer-1", "answer": {"text": "我主导了架构设计", "inputMode": "text", "durationMs": 1000},
    })
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["state"] == "questioning"
    assert first_body["feedback"]["verdict"] in ("strong", "partial", "weak")
    assert first_body["feedback"]["summary"]
    assert first_body["nextQuestion"]["id"] != question_id

    second = client.post("/api/interview", json={
        "action": "answer", "interviewId": interview_id, "questionId": first_body["nextQuestion"]["id"],
        "clientAnswerId": "answer-2", "answer": {"text": "我优化了响应延迟", "inputMode": "text"},
    })
    assert second.status_code == 200, second.text
    assert second.json()["state"] == "completed"
    assert second.json()["reportReady"] is True

    report = client.post("/api/interview", json={"action": "report", "interviewId": interview_id})
    assert report.status_code == 200, report.text
    report_body = report.json()
    assert report_body["state"] == "completed"
    assert report_body["overallScore"] == 80
    assert len(report_body["dimensions"]) == 6
    assert len(report_body["turns"]) == 2
    assert report_body["readiness"] in ("ready", "borderline", "not_ready")


def test_snapshot_and_review_flow():
    client = make_client()
    start = client.post("/api/interview", json={
        "action": "start", "clientSessionId": "client-1",
        "config": {"questionCount": 2, "focus": "knowledge"},
        "materials": {"jd": "高级 Go 工程师\n要求熟悉 Go 并发"},
    })
    interview_id = start.json()["interviewId"]
    question_id = start.json()["question"]["id"]
    client.post("/api/interview", json={
        "action": "answer", "interviewId": interview_id, "questionId": question_id,
        "clientAnswerId": "a1", "answer": {"text": "我负责架构设计", "inputMode": "text"},
    })

    snapshot = client.get(f"/api/v1/interviews/{interview_id}")
    assert snapshot.status_code == 200
    snap = snapshot.json()
    assert snap["interviewId"] == interview_id
    assert len(snap["turns"]) == 1
    assert snap["currentQuestion"]["id"]

    review = client.get(f"/api/v1/interviews/{interview_id}/review")
    assert review.status_code == 200
    rev = review.json()
    assert rev["schemaVersion"] == "1.0.0"
    assert len(rev["turns"]) == 1
    assert rev["turns"][0]["answer"]
