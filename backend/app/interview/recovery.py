"""Public recovery projections mirroring Go interview/recovery.go."""
from __future__ import annotations

from datetime import datetime

from ..llm.schema import StrictModel
from .sources import knowledge_question_label, knowledge_reference_text
from .types import (
    AnswerFeedback,
    AnswerPayload,
    EvidenceRef,
    InterviewState,
    Progress,
    Profile,
    Question,
    SourceKind,
)


class SnapshotTurn(StrictModel):
    question: Question
    answer: AnswerPayload
    feedback: AnswerFeedback


class SessionSnapshot(StrictModel):
    interviewId: str
    state: InterviewState
    profile: Profile
    currentQuestion: Question | None = None
    turns: list[SnapshotTurn] = []
    progress: Progress
    reportReady: bool


class ReviewReference(StrictModel):
    evidenceId: str
    title: str
    answer: str
    locator: str = ""


class ReviewTurn(StrictModel):
    question: Question
    answer: AnswerPayload
    feedback: AnswerFeedback
    references: list[ReviewReference] = []
    answeredAt: datetime


class ReviewSnapshot(StrictModel):
    schemaVersion: str
    interviewId: str
    state: InterviewState
    startedAt: datetime
    generatedAt: datetime
    turns: list[ReviewTurn] = []


class SessionEventSummary(StrictModel):
    eventId: str
    sequence: int
    commandId: str = ""
    type: str
    createdAt: datetime


class SessionEventPage(StrictModel):
    interviewId: str
    events: list[SessionEventSummary] = []
    nextSequence: int


def review_references(refs: list[EvidenceRef]) -> list[ReviewReference]:
    result: list[ReviewReference] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.kind != SourceKind.KNOWLEDGE:
            continue
        answer = knowledge_reference_text(ref.quote)
        if not answer:
            continue
        if ref.anchorId in seen:
            continue
        seen.add(ref.anchorId)
        result.append(ReviewReference(
            evidenceId=ref.anchorId, title=knowledge_question_label(ref.quote),
            answer=answer, locator=ref.locator,
        ))
    return result
