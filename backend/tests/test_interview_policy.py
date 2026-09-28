"""P4d deterministic policy + scoring tests (mirrors Go policy_test.go)."""
from datetime import datetime, timezone

from app.interview.policy import (
    choose_follow_up_axis,
    derive_policy,
    normalize_assessment,
    question_kind,
)
from app.interview.scoring import score_report, uses_knowledge_rubric
from app.interview.types import (
    AnswerPayload,
    AnswerRecord,
    Assessment,
    ClaimCheck,
    ClaimVerdict,
    CoveragePoint,
    Difficulty,
    Focus,
    InputMode,
    InterviewConfig,
    InterviewSession,
    InterviewState,
    PolicyAction,
    PolicyDecision,
    Profile,
    Question,
    QuestionAdaptation,
    QuestionKind,
    SourceKind,
)

NOW = datetime.now(timezone.utc)


def policy_session(area: Focus) -> InterviewSession:
    return InterviewSession(
        id="i1", clientSessionId="c1", config=InterviewConfig(questionCount=3),
        state=InterviewState.AWAITING_ANSWER,
        profile=Profile(coverage=[CoveragePoint(id="coverage-1", area=area, label="l")]),
        startedAt=NOW,
        version=1,
        currentQuestion=Question(
            id="question-1", rootId="root-1", coveragePointId="coverage-1", text="",
            kind=QuestionKind.KNOWLEDGE, difficulty=Difficulty.MEDIUM,
            adaptation=QuestionAdaptation(trigger=PolicyAction.INITIAL, reason="", depth=0),
        ),
    )


def assessment(**kwargs):
    base = dict(correctness=4, depth=4, specificity=4, ownership=4, metrics=4, tradeoffs=4)
    base.update(kwargs)
    return Assessment(**base)


def test_knowledge_gap_uses_knowledge_follow_up_axis():
    decision = derive_policy(policy_session(Focus.KNOWLEDGE), assessment(depth=2))
    assert decision.action == PolicyAction.FOLLOW_UP
    assert decision.followUpAxis == "boundary"


def test_project_gap_is_not_promoted_to_strong_answer():
    decision = derive_policy(policy_session(Focus.PROJECTS), assessment(correctness=5, depth=5, specificity=5, ownership=1, metrics=1, tradeoffs=5))
    assert decision.action == PolicyAction.FOLLOW_UP
    assert decision.followUpAxis == "ownership"


def test_contradicted_project_claim_triggers_verification():
    decision = derive_policy(
        policy_session(Focus.PROJECTS),
        assessment(claimChecks=[ClaimCheck(claim="延迟口径", verdict=ClaimVerdict.CONTRADICTED)]),
    )
    assert decision.action == PolicyAction.FOLLOW_UP
    assert decision.followUpAxis == "verification"


def test_factual_error_triggers_prerequisite_then_advance():
    session = policy_session(Focus.KNOWLEDGE)
    decision = derive_policy(session, assessment(correctness=1))
    assert decision.action == PolicyAction.PREREQUISITE
    assert decision.followUpDepth == 1

    # depth already at 2 -> advance instead
    session.currentQuestion.adaptation.depth = 2
    decision = derive_policy(session, assessment(correctness=1))
    assert decision.action == PolicyAction.ADVANCE
    assert decision.followUpDepth == 0


def test_question_count_reached_completes():
    session = policy_session(Focus.KNOWLEDGE)
    session.answers = [AnswerRecord(
        question=session.currentQuestion, answer=AnswerPayload(text="a", inputMode=InputMode.TEXT),
        assessment=assessment(), decision=PolicyDecision(
            action=PolicyAction.INITIAL, reason="", difficulty=Difficulty.MEDIUM,
            coveragePointId="coverage-1", rootId="root-1", followUpDepth=0,
        ), answeredAt=NOW,
    )]
    session.answers.append(session.answers[0].model_copy(deep=True))
    decision = derive_policy(session, assessment())
    assert decision.action == PolicyAction.COMPLETE


def test_strong_answer_raises_difficulty():
    decision = derive_policy(policy_session(Focus.KNOWLEDGE), assessment(correctness=5, depth=5, specificity=5))
    assert decision.action == PolicyAction.ADVANCE
    assert decision.difficulty == Difficulty.HARD


def test_normalize_assessment_clamps_scores():
    normalized = normalize_assessment(assessment(correctness=9, depth=0))
    assert normalized.correctness == 5
    assert normalized.depth == 1


def test_knowledge_score_excludes_project_only_fields():
    records = [AnswerRecord(
        question=Question(
            id="q", rootId="r", text="", kind=QuestionKind.KNOWLEDGE, difficulty=Difficulty.MEDIUM,
            coveragePointId="c", adaptation=QuestionAdaptation(trigger=PolicyAction.INITIAL),
        ),
        answer=AnswerPayload(text="a", inputMode=InputMode.TEXT),
        assessment=assessment(correctness=5, depth=5, specificity=5, ownership=1, metrics=1, tradeoffs=5),
        decision=PolicyDecision(action=PolicyAction.INITIAL, reason="", difficulty=Difficulty.MEDIUM, coveragePointId="c", rootId="r", followUpDepth=0),
        answeredAt=NOW,
    )]
    assert score_report(records) == 100


def test_follow_up_uses_knowledge_rubric_without_resume_evidence():
    question = Question(
        id="q", rootId="r", text="", kind=QuestionKind.FOLLOW_UP, difficulty=Difficulty.MEDIUM,
        coveragePointId="c", adaptation=QuestionAdaptation(trigger=PolicyAction.FOLLOW_UP),
        evidenceRefs=[{"sourceId": "k", "kind": SourceKind.KNOWLEDGE, "anchorId": "a", "locator": "l", "quote": "q"}],
    )
    assert uses_knowledge_rubric(question)
    record = AnswerRecord(
        question=question, answer=AnswerPayload(text="a", inputMode=InputMode.TEXT),
        assessment=assessment(correctness=5, depth=5, specificity=5, ownership=1, metrics=1, tradeoffs=5),
        decision=PolicyDecision(action=PolicyAction.FOLLOW_UP, reason="", difficulty=Difficulty.MEDIUM, coveragePointId="c", rootId="r", followUpDepth=1),
        answeredAt=NOW,
    )
    assert score_report([record]) == 100


def test_score_report_uses_half_away_from_zero_rounding():
    # 22.5 must round up to 23; Python's banker's rounding would give 22.
    records = []
    for scores in ([1, 1, 1, 1], [2, 1, 1, 1]):
        records.append(AnswerRecord(
            question=Question(
                id="q", rootId="r", text="", kind=QuestionKind.KNOWLEDGE, difficulty=Difficulty.MEDIUM,
                coveragePointId="c", adaptation=QuestionAdaptation(trigger=PolicyAction.INITIAL),
            ),
            answer=AnswerPayload(text="a", inputMode=InputMode.TEXT),
            assessment=Assessment(
                correctness=scores[0], depth=scores[1], specificity=scores[2], ownership=1,
                metrics=1, tradeoffs=scores[3],
            ),
            decision=PolicyDecision(action=PolicyAction.INITIAL, reason="", difficulty=Difficulty.MEDIUM, coveragePointId="c", rootId="r", followUpDepth=0),
            answeredAt=NOW,
        ))
    assert score_report(records) == 23


def test_choose_follow_up_axis_falls_back_to_specificity():
    session = policy_session(Focus.KNOWLEDGE)
    # mark principle/boundary/example as used via prior answers
    for axis in ("principle", "boundary", "example"):
        session.answers.append(AnswerRecord(
            question=Question(
                id="q", rootId="root-1", text="", kind=QuestionKind.FOLLOW_UP, difficulty=Difficulty.MEDIUM,
                coveragePointId="coverage-1", adaptation=QuestionAdaptation(trigger=PolicyAction.FOLLOW_UP, followUpAxis=axis),
            ),
            answer=AnswerPayload(text="a", inputMode=InputMode.TEXT),
            assessment=assessment(),
            decision=PolicyDecision(action=PolicyAction.FOLLOW_UP, reason="", difficulty=Difficulty.MEDIUM, coveragePointId="coverage-1", rootId="root-1", followUpAxis=axis, followUpDepth=1),
            answeredAt=NOW,
        ))
    assert choose_follow_up_axis(session, assessment(), Focus.KNOWLEDGE) == "specificity"


def test_question_kind_mapping():
    point = CoveragePoint(id="p", area=Focus.PROJECTS, label="l")
    decision = PolicyDecision(action=PolicyAction.ADVANCE, reason="", difficulty=Difficulty.MEDIUM, coveragePointId="p", rootId="r", followUpDepth=0)
    assert question_kind(point, decision) == QuestionKind.PROJECT
    decision.action = PolicyAction.FOLLOW_UP
    assert question_kind(point, decision) == QuestionKind.FOLLOW_UP
    decision.action = PolicyAction.PREREQUISITE
    assert question_kind(point, decision) == QuestionKind.PREREQUISITE
    point.area = Focus.KNOWLEDGE
    decision.action = PolicyAction.ADVANCE
    assert question_kind(point, decision) == QuestionKind.KNOWLEDGE
