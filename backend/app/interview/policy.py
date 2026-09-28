"""Deterministic adaptive policy mirroring Go interview/policy.go."""
from __future__ import annotations

from .types import (
    Assessment,
    ClaimVerdict,
    CoveragePoint,
    Difficulty,
    Focus,
    InterviewSession,
    PolicyAction,
    PolicyDecision,
    QuestionKind,
)


def normalize_assessment(assessment: Assessment) -> Assessment:
    assessment.correctness = _clamp_score(assessment.correctness)
    assessment.depth = _clamp_score(assessment.depth)
    assessment.specificity = _clamp_score(assessment.specificity)
    assessment.ownership = _clamp_score(assessment.ownership)
    assessment.metrics = _clamp_score(assessment.metrics)
    assessment.tradeoffs = _clamp_score(assessment.tradeoffs)
    return assessment


def derive_policy(session: InterviewSession, assessment: Assessment) -> PolicyDecision:
    current = session.currentQuestion
    area = coverage_point_by_id(session.profile, current.coveragePointId).area
    depth = current.adaptation.depth
    decision = PolicyDecision(
        action=PolicyAction.ADVANCE,
        reason="",
        difficulty=current.difficulty,
        coveragePointId=current.coveragePointId,
        rootId=current.rootId,
        followUpDepth=depth,
    )

    if len(session.answers) + 1 >= session.config.questionCount:
        decision.action = PolicyAction.COMPLETE
        decision.reason = "configured question count reached"
        return decision

    if assessment.factualErrors or assessment.correctness <= 1:
        if depth < 2:
            decision.action = PolicyAction.PREREQUISITE
            decision.reason = "factual error detected; verify the prerequisite before continuing"
            decision.difficulty = _lower_difficulty(current.difficulty)
            decision.followUpDepth = depth + 1
            return decision
        decision.action = PolicyAction.ADVANCE
        decision.reason = "root follow-up limit reached after prerequisite remediation"
        decision.difficulty = _lower_difficulty(current.difficulty)
        decision.followUpDepth = 0
        return decision

    if area == Focus.PROJECTS and _has_claim_verdict(assessment, ClaimVerdict.CONTRADICTED) and depth < 2:
        decision.action = PolicyAction.FOLLOW_UP
        decision.reason = "answer contradicts supplied resume evidence; request reconciliation"
        decision.followUpAxis = "verification"
        decision.followUpDepth = depth + 1
        return decision

    if vague_assessment_for_area(area, assessment) and depth < 2:
        axis = choose_follow_up_axis(session, assessment, area)
        decision.action = PolicyAction.FOLLOW_UP
        decision.reason = "answer is too general; request verifiable detail"
        decision.followUpAxis = axis
        decision.followUpDepth = depth + 1
        return decision

    decision.action = PolicyAction.ADVANCE
    decision.followUpDepth = 0
    if strong_assessment_for_area(area, assessment):
        decision.reason = "strong answer; rotate coverage and increase difficulty"
        decision.difficulty = _raise_difficulty(current.difficulty)
    elif depth >= 2:
        decision.reason = "root follow-up limit reached; rotate coverage"
    else:
        decision.reason = "answer is sufficient; rotate to the next coverage point"
    return decision


def vague_assessment(assessment: Assessment) -> bool:
    return (
        assessment.specificity <= 2
        or assessment.ownership <= 2
        or assessment.metrics <= 2
        or assessment.tradeoffs <= 2
    )


def vague_assessment_for_area(area: Focus, assessment: Assessment) -> bool:
    if area == Focus.KNOWLEDGE:
        return assessment.correctness <= 2 or assessment.depth <= 2 or assessment.specificity <= 2
    return vague_assessment(assessment)


def strong_assessment(assessment: Assessment) -> bool:
    return (
        assessment.correctness >= 4
        and assessment.depth >= 4
        and assessment.specificity >= 4
        and not assessment.factualErrors
    )


def strong_assessment_for_area(area: Focus, assessment: Assessment) -> bool:
    if not strong_assessment(assessment):
        return False
    if area == Focus.PROJECTS:
        return assessment.ownership >= 4 and assessment.metrics >= 3 and assessment.tradeoffs >= 3
    return True


def has_claim_verdict(assessment: Assessment, verdict: ClaimVerdict) -> bool:
    return any(check.verdict == verdict for check in assessment.claimChecks)


_has_claim_verdict = has_claim_verdict


def choose_follow_up_axis(session: InterviewSession, assessment: Assessment, area: Focus) -> str:
    used: set[str] = set()
    for record in session.answers:
        if record.question.rootId == session.currentQuestion.rootId and record.decision.followUpAxis:
            used.add(record.decision.followUpAxis)
    if session.currentQuestion.adaptation.followUpAxis:
        used.add(session.currentQuestion.adaptation.followUpAxis)

    if area == Focus.KNOWLEDGE:
        candidates = [
            ("principle", assessment.correctness),
            ("boundary", assessment.depth),
            ("example", assessment.specificity),
        ]
    else:
        candidates = [
            ("ownership", assessment.ownership),
            ("metrics", assessment.metrics),
            ("tradeoff", assessment.tradeoffs),
        ]
    for name, score in candidates:
        if score <= 2 and name not in used:
            return name
    for name, _score in candidates:
        if name not in used:
            return name
    return "specificity"


def question_kind(point: CoveragePoint, decision: PolicyDecision) -> QuestionKind:
    if decision.action == PolicyAction.PREREQUISITE:
        return QuestionKind.PREREQUISITE
    if decision.action == PolicyAction.FOLLOW_UP:
        return QuestionKind.FOLLOW_UP
    if point.area == Focus.PROJECTS:
        return QuestionKind.PROJECT
    return QuestionKind.KNOWLEDGE


def _lower_difficulty(value: Difficulty) -> Difficulty:
    if value == Difficulty.HARD:
        return Difficulty.MEDIUM
    if value == Difficulty.MEDIUM:
        return Difficulty.EASY
    return Difficulty.EASY


def _raise_difficulty(value: Difficulty) -> Difficulty:
    if value == Difficulty.EASY:
        return Difficulty.MEDIUM
    if value == Difficulty.MEDIUM:
        return Difficulty.HARD
    return Difficulty.HARD


def _clamp_score(value: int) -> int:
    if value < 1:
        return 1
    if value > 5:
        return 5
    return value


def coverage_point_by_id(profile, id: str) -> CoveragePoint:
    for point in profile.coverage:
        if point.id == id:
            return point
    return profile.coverage[0]
