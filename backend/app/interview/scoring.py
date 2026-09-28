"""Report scoring mirroring Go interview/service.go scoreReport (floor(x+0.5) rounding)."""
from __future__ import annotations

import math

from .types import AnswerRecord, Question, QuestionKind, SourceKind


def score_report(records: list[AnswerRecord]) -> int:
    if not records:
        return 0
    total = 0.0
    for record in records:
        assessment = record.assessment
        if uses_knowledge_rubric(record.question):
            score = assessment.correctness + assessment.depth + assessment.specificity + assessment.tradeoffs
            total += score / 20 * 100
            continue
        score = (
            assessment.correctness
            + assessment.depth
            + assessment.specificity
            + assessment.ownership
            + assessment.metrics
            + assessment.tradeoffs
        )
        total += score / 30 * 100
    return int(math.floor(total / len(records) + 0.5))


def uses_knowledge_rubric(question: Question) -> bool:
    if question.kind == QuestionKind.KNOWLEDGE:
        return True
    if question.kind in (QuestionKind.PROJECT, QuestionKind.BEHAVIORAL):
        return False
    if question.kind in (QuestionKind.FOLLOW_UP, QuestionKind.PREREQUISITE):
        return not has_resume_evidence(question)
    return False


def has_resume_evidence(question: Question) -> bool:
    return any(ref.kind == SourceKind.RESUME for ref in question.evidenceRefs)
