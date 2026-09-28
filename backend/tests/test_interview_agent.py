"""P4c harness interview agent adapter tests."""
import pytest

from app.harness import Runtime
from app.harness.interview_agent import (
    ASSESSOR_PROMPT,
    INTERVIEWER_PROMPT,
    PLANNER_PROMPT,
    REPORTER_PROMPT,
    InterviewAgent,
    _harness_duration_env,
    interview_agent_options_from_env,
)
from app.interview.types import (
    AnswerPayload,
    AssessAnswerRequest,
    Assessment,
    CoverageCandidate,
    CoverageSelection,
    Difficulty,
    Focus,
    GenerateQuestionRequest,
    GenerateReportRequest,
    InputMode,
    InterviewConfig,
    PlanCoverageRequest,
    PolicyAction,
    PolicyDecision,
    Profile,
    Question,
    QuestionAdaptation,
    QuestionDraft,
    QuestionKind,
    ReportDraft,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def chat_json(self, messages, out_model):
        self.calls.append((messages, out_model))
        return out_model.model_validate(self.responses[out_model])


def make_question(coverage="c1", id="q1"):
    return Question(
        id=id, rootId="r1", text="问题", kind=QuestionKind.KNOWLEDGE,
        difficulty=Difficulty.MEDIUM, coveragePointId=coverage,
        adaptation=QuestionAdaptation(trigger=PolicyAction.INITIAL, reason="", depth=0),
    )


def make_assessment():
    return Assessment(correctness=4, depth=4, specificity=4, ownership=4, metrics=4, tradeoffs=4)


def make_question_request():
    return GenerateQuestionRequest(
        profile=Profile(),
        decision=PolicyDecision(
            action=PolicyAction.INITIAL, reason="r", difficulty=Difficulty.MEDIUM,
            coveragePointId="c1", rootId="r1", followUpDepth=0,
        ),
    )


def make_plan_request(candidates=None, current="c1"):
    if candidates is None:
        candidates = [
            CoverageCandidate(coveragePointId="c1", area=Focus.KNOWLEDGE, label="l1", priority=50, questionCount=0, lastAskedTurn=0),
            CoverageCandidate(coveragePointId="c2", area=Focus.PROJECTS, label="l2", priority=60, questionCount=0, lastAskedTurn=0),
        ]
    return PlanCoverageRequest(
        config=InterviewConfig(),
        currentCoveragePointId=current,
        previousQuestion=make_question(),
        previousAssessment=make_assessment(),
        candidates=candidates,
    )


def make_agent(responses=None):
    client = FakeClient(responses or {})
    runtime = Runtime(client)
    agent = InterviewAgent(runtime)
    return agent, client, runtime


async def test_registers_default_agents_with_timeouts():
    agent, _, runtime = make_agent()
    assert runtime.agent("interviewer").timeout == 90.0
    assert runtime.agent("assessor").timeout == 180.0
    assert runtime.agent("reporter").timeout == 90.0
    assert runtime.agent("coverage_planner").timeout == 90.0
    assert "禁止泛泛问" in runtime.agent("interviewer").system_prompt


async def test_prompts_are_verbatim_markers():
    assert INTERVIEWER_PROMPT.startswith("你是 OfferPilot 的资深技术面试官子 Agent。")
    assert "严禁按回答字数、关键词命中、连接词、术语数量或固定模板机械打分" in ASSESSOR_PROMPT
    assert "coveragePointId 必须逐字复制某个 candidate.coveragePointId" in PLANNER_PROMPT
    assert "只汇总 request.answers 中已经提交的 Assessment" in REPORTER_PROMPT


async def test_generate_question():
    agent, client, _ = make_agent({QuestionDraft: {"text": "生成的题目", "evidenceRefs": []}})
    result = await agent.generate_question(make_question_request())
    assert result.text == "生成的题目"
    assert client.calls[0][1] is QuestionDraft
    system_prompt = client.calls[0][0][0].content
    assert system_prompt == INTERVIEWER_PROMPT


async def test_assess_answer():
    data = {
        "correctness": 4, "depth": 4, "specificity": 4, "ownership": 4, "metrics": 4, "tradeoffs": 4,
        "factualErrors": [], "strengths": [], "gaps": [], "evidenceRefs": [], "claimChecks": [],
    }
    agent, client, _ = make_agent({Assessment: data})
    request = AssessAnswerRequest(question=make_question(), answer=AnswerPayload(text="答", inputMode=InputMode.TEXT))
    result = await agent.assess_answer(request)
    assert result.correctness == 4
    assert client.calls[0][1] is Assessment
    assert "Populate every rubric score" in client.calls[0][0][1].content


async def test_generate_report():
    agent, client, _ = make_agent({ReportDraft: {"summary": "总评", "strengths": [], "gaps": [], "evidenceRefs": []}})
    result = await agent.generate_report(GenerateReportRequest(profile=Profile()))
    assert result.summary == "总评"
    assert client.calls[0][1] is ReportDraft


async def test_plan_coverage_happy_path():
    agent, client, _ = make_agent({CoverageSelection: {"coveragePointId": "c2", "reason": "r", "signals": ["s"]}})
    result = await agent.plan_coverage(make_plan_request())
    assert result.coveragePointId == "c2"
    assert client.calls[0][1] is CoverageSelection


async def test_plan_coverage_rejects_empty_candidates():
    agent, _, _ = make_agent()
    with pytest.raises(ValueError, match="at least one candidate"):
        await agent.plan_coverage(make_plan_request(candidates=[]))


async def test_plan_coverage_rejects_empty_candidate_id():
    agent, _, _ = make_agent()
    request = make_plan_request(candidates=[
        CoverageCandidate(coveragePointId="  ", area=Focus.KNOWLEDGE, label="l", priority=50, questionCount=0, lastAskedTurn=0),
    ])
    with pytest.raises(ValueError, match="candidate id is required"):
        await agent.plan_coverage(request)


async def test_plan_coverage_rejects_duplicate_candidate_ids():
    agent, _, _ = make_agent()
    request = make_plan_request(candidates=[
        CoverageCandidate(coveragePointId="c1", area=Focus.KNOWLEDGE, label="l", priority=50, questionCount=0, lastAskedTurn=0),
        CoverageCandidate(coveragePointId="c1", area=Focus.KNOWLEDGE, label="l", priority=50, questionCount=0, lastAskedTurn=0),
    ])
    with pytest.raises(ValueError, match="duplicate coverage candidate"):
        await agent.plan_coverage(request)


async def test_plan_coverage_rejects_unknown_selection():
    agent, _, _ = make_agent({CoverageSelection: {"coveragePointId": "nope", "reason": "r", "signals": ["s"]}})
    with pytest.raises(ValueError, match="unknown candidate"):
        await agent.plan_coverage(make_plan_request())


async def test_plan_coverage_rejects_reselect_when_alternative_exists():
    agent, _, _ = make_agent({CoverageSelection: {"coveragePointId": "c1", "reason": "r", "signals": ["s"]}})
    with pytest.raises(ValueError, match="reselected current candidate"):
        await agent.plan_coverage(make_plan_request())


async def test_plan_coverage_rejects_empty_reason_and_signals():
    agent, _, _ = make_agent({CoverageSelection: {"coveragePointId": "c2", "reason": "  ", "signals": ["s"]}})
    with pytest.raises(ValueError, match="empty reason"):
        await agent.plan_coverage(make_plan_request())

    agent, _, _ = make_agent({CoverageSelection: {"coveragePointId": "c2", "reason": "r", "signals": []}})
    with pytest.raises(ValueError, match="no selection signals"):
        await agent.plan_coverage(make_plan_request())

    agent, _, _ = make_agent({CoverageSelection: {"coveragePointId": "c2", "reason": "r", "signals": [" "]}})
    with pytest.raises(ValueError, match="empty selection signal"):
        await agent.plan_coverage(make_plan_request())


def test_harness_duration_env(monkeypatch):
    monkeypatch.delenv("OFFERPILOT_INTERVIEWER_TIMEOUT", raising=False)
    assert _harness_duration_env("OFFERPILOT_INTERVIEWER_TIMEOUT", 90.0) == 90.0
    monkeypatch.setenv("OFFERPILOT_INTERVIEWER_TIMEOUT", "90s")
    assert _harness_duration_env("OFFERPILOT_INTERVIEWER_TIMEOUT", 90.0) == 90.0
    monkeypatch.setenv("OFFERPILOT_INTERVIEWER_TIMEOUT", "90000")
    assert _harness_duration_env("OFFERPILOT_INTERVIEWER_TIMEOUT", 90.0) == 90.0
    monkeypatch.setenv("OFFERPILOT_INTERVIEWER_TIMEOUT", "1m30s")
    assert _harness_duration_env("OFFERPILOT_INTERVIEWER_TIMEOUT", 90.0) == 90.0
    monkeypatch.setenv("OFFERPILOT_INTERVIEWER_TIMEOUT", "abc")
    assert _harness_duration_env("OFFERPILOT_INTERVIEWER_TIMEOUT", 90.0) == 90.0


def test_options_from_env(monkeypatch):
    monkeypatch.setenv("OFFERPILOT_ASSESSOR_TIMEOUT", "180s")
    options = interview_agent_options_from_env()
    assert options.assessor_timeout == 180.0
    assert options.interviewer_timeout == 90.0


def test_plan_request_marshals_question_kind_counts_as_strings():
    request = PlanCoverageRequest(
        config=InterviewConfig(),
        currentCoveragePointId="c1",
        previousQuestion=make_question(),
        previousAssessment=make_assessment(),
        candidates=[CoverageCandidate(coveragePointId="c1", area=Focus.KNOWLEDGE, label="l", priority=50, questionCount=0, lastAskedTurn=0)],
        questionKindCounts={QuestionKind.KNOWLEDGE: 1},
    )
    dumped = request.model_dump(mode="json")
    assert dumped["questionKindCounts"] == {"knowledge": 1}
