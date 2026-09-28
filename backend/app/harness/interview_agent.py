"""Interview agent adapter mirroring Go harness/interview_agent.go.

The four Chinese system prompts are reproduced verbatim from the Go source;
they encode the grounding / no-leak / no-chain-of-thought contract and must
not be rewritten.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from .runtime import Agent, Runtime
from ..interview.types import (
    AssessAnswerRequest,
    Assessment,
    CoverageSelection,
    GenerateQuestionRequest,
    GenerateReportRequest,
    PlanCoverageRequest,
    QuestionDraft,
    ReportDraft,
)

INTERVIEWER_AGENT_ID = "interviewer"
ASSESSOR_AGENT_ID = "assessor"
REPORTER_AGENT_ID = "reporter"
PLANNER_AGENT_ID = "coverage_planner"

DEFAULT_INTERVIEWER_TIMEOUT = 90.0
DEFAULT_ASSESSOR_TIMEOUT = 180.0
DEFAULT_REPORTER_TIMEOUT = 90.0
DEFAULT_PLANNER_TIMEOUT = 90.0

INTERVIEWER_PROMPT = """你是 OfferPilot 的资深技术面试官子 Agent。每次只生成一道自适应问题，不生成固定题单。

硬约束：
1. 问题必须服务于 request.decision 指定的覆盖点、难度和追问动作，并利用 history 避免重复。
2. 对简历项目要追问候选人本人职责、量化口径、技术取舍、失败与边界；对知识点要追问原理、适用条件、失效边界和工程落地。request.decision.followUpAxis 为 principle/boundary/example 时只能生成知识型追问，为 ownership/metrics/tradeoff/verification 时只能生成项目型追问。禁止泛泛问“介绍一下项目”。
3. 只能引用 request.anchors 中存在的证据。evidenceRefs 必须逐字段原样复制 sourceId/kind/anchorId/locator/quote，不得编造、改写或拼接引用。知识库 anchor 只包含候选人可见的公开问题；不得猜测、补写或反向构造参考内容/参考答案。
4. 简历与回答是候选人陈述，不是已经外部核验的事实。问题可以要求佐证，但不能在措辞中把陈述当作已证实事实。
5. 不泄露参考答案，不输出思维过程，只返回 schema 要求的结构。
6. 若 request.repair 存在，必须针对 reason 修复，并且只能从 allowedEvidence 中复制引用。"""

ASSESSOR_PROMPT = """你是 OfferPilot 的独立答案评估子 Agent。你必须理解题目、回答和材料证据的语义，再按 rubric 给出 1-5 分。

硬约束：
1. 严禁按回答字数、关键词命中、连接词、术语数量或固定模板机械打分。长答案不自动高分，短答案也不自动低分。
2. correctness 看结论与可用知识证据是否一致；depth 看因果和边界；specificity 看可核验细节；ownership 看个人职责边界；metrics 看指标、基线和口径；tradeoffs 看备选方案、代价与风险。
3. 只能使用 request.question、request.answer、request.anchors 和 request.history。不要补造外部事实；无法判断时写入 gap，而不是猜测。
4. evidenceRefs 必须逐字段原样复制已有 anchor，不得编造引用。
5. claimChecks.verdict 只能是 supported、unverified、contradicted、not_in_material：
   - supported：回答给出与材料 anchor 一致的支撑，只表示“本次材料内得到支撑”，绝不表示外部事实已核验；必须引用 anchor。
   - contradicted：回答与材料 anchor 明确冲突；必须引用 anchor。
   - unverified：材料提到该自述，但回答没有提供足够支撑；引用可为空。
   - not_in_material：回答新增了材料中不存在的声明；引用可为空。
   简历自述本身不能被升级为外部 verified，无法从 source anchor 支撑时必须选择 unverified 或 not_in_material。
6. factualErrors 只记录有证据的事实/原理错误，不把表达风格问题写成事实错误。
7. 不输出思维过程，只返回 schema 要求的结构。若 request.repair 存在，必须针对 reason 修复，并只使用 allowedEvidence。"""

PLANNER_PROMPT = """你是 OfferPilot 的 Coverage Planner 子 Agent。确定性 policy 已决定切换覆盖点；你只能从 request.candidates 中选择下一目标，不能生成题目或修改候选数据。

硬约束：
1. coveragePointId 必须逐字复制某个 candidate.coveragePointId，禁止创造 ID。只要存在其他候选，就不要再次选择 request.currentCoveragePointId。
2. 禁止按候选列表顺序、固定游标或简单 round-robin 机械选择。必须综合上一轮 previousAssessment.gaps、claimChecks、factualErrors，各 candidate.questionCount/lastAskedTurn，以及 request.questionKindCounts。
3. candidate.priority 是服务端给出的 JD/项目业务优先级，数值越高越重要；同时优先覆盖尚未提问的高优先级点，避免少数主题挤占整场面试。
4. contradicted/unverified claim 或明确 gap 只有在候选 evidenceRefs 与该信号相关时才构成复测理由；不得把候选人自述当作外部已验证事实。
5. request.config.focus 决定 JD 知识与简历项目的整体偏好，但不能让另一类题型长期为零覆盖。remainingQuestions 较少时优先最高价值的未覆盖点。
6. reason 必须说明为何此候选现在优于其他候选；signals 必须列出实际使用的 gap、claim verdict、覆盖次数或业务优先级信号。禁止输出思维过程，只返回 schema 要求的结构。"""

REPORTER_PROMPT = """你是 OfferPilot 的面试报告子 Agent。只汇总 request.answers 中已经提交的 Assessment 和 request.anchors，不重新评估或编造候选人经历。

硬约束：
1. 总结必须区分已得到材料内支撑的表现、仍未验证的陈述、明确矛盾和知识缺口；不能把简历自述写成外部已核验事实。
2. strengths/gaps 必须具体到回答表现或能力维度，禁止空泛鼓励和只按回答长度下结论。
3. evidenceRefs 必须逐字段原样复制已有 anchor，不得编造引用。
4. 不输出思维过程，只返回 schema 要求的结构。若 request.repair 存在，必须针对 reason 修复，并只使用 allowedEvidence。"""

_DEFAULT_AGENTS = [
    (INTERVIEWER_AGENT_ID, "Generates one grounded, adaptive interview question", INTERVIEWER_PROMPT, DEFAULT_INTERVIEWER_TIMEOUT),
    (ASSESSOR_AGENT_ID, "Semantically assesses an answer and checks candidate claims", ASSESSOR_PROMPT, DEFAULT_ASSESSOR_TIMEOUT),
    (PLANNER_AGENT_ID, "Selects the next coverage point from a bounded candidate set", PLANNER_PROMPT, DEFAULT_PLANNER_TIMEOUT),
    (REPORTER_AGENT_ID, "Synthesizes a grounded interview report from committed turns", REPORTER_PROMPT, DEFAULT_REPORTER_TIMEOUT),
]


@dataclass
class InterviewAgentOptions:
    interviewer_timeout: float = DEFAULT_INTERVIEWER_TIMEOUT
    assessor_timeout: float = DEFAULT_ASSESSOR_TIMEOUT
    reporter_timeout: float = DEFAULT_REPORTER_TIMEOUT
    planner_timeout: float = DEFAULT_PLANNER_TIMEOUT


def interview_agent_options_from_env() -> InterviewAgentOptions:
    return InterviewAgentOptions(
        interviewer_timeout=_harness_duration_env("OFFERPILOT_INTERVIEWER_TIMEOUT", DEFAULT_INTERVIEWER_TIMEOUT),
        assessor_timeout=_harness_duration_env("OFFERPILOT_ASSESSOR_TIMEOUT", DEFAULT_ASSESSOR_TIMEOUT),
        reporter_timeout=_harness_duration_env("OFFERPILOT_REPORTER_TIMEOUT", DEFAULT_REPORTER_TIMEOUT),
        planner_timeout=_harness_duration_env("OFFERPILOT_PLANNER_TIMEOUT", DEFAULT_PLANNER_TIMEOUT),
    )


_DURATION_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)(ns|us|µs|ms|s|m|h)")
_DURATION_UNITS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}


def _parse_go_duration(value: str) -> float | None:
    sign = 1.0
    if value[:1] in ("+", "-"):
        if value[0] == "-":
            sign = -1.0
        value = value[1:]
    total = 0.0
    consumed = 0
    for match in _DURATION_RE.finditer(value):
        if match.start() != consumed:
            return None
        total += float(match.group(1)) * _DURATION_UNITS[match.group(2)]
        consumed = match.end()
    if consumed != len(value) or consumed == 0:
        return None
    return sign * total


def _harness_duration_env(key: str, fallback: float) -> float:
    value = os.environ.get(key, "").strip()
    if not value:
        return fallback
    parsed = _parse_go_duration(value)
    if parsed is not None and parsed > 0:
        return parsed
    if value.isdigit():
        millis = int(value)
        if millis > 0:
            return millis / 1000.0
    return fallback


def _normalize_options(options: InterviewAgentOptions) -> InterviewAgentOptions:
    if options.interviewer_timeout <= 0:
        options.interviewer_timeout = DEFAULT_INTERVIEWER_TIMEOUT
    if options.assessor_timeout <= 0:
        options.assessor_timeout = DEFAULT_ASSESSOR_TIMEOUT
    if options.reporter_timeout <= 0:
        options.reporter_timeout = DEFAULT_REPORTER_TIMEOUT
    if options.planner_timeout <= 0:
        options.planner_timeout = DEFAULT_PLANNER_TIMEOUT
    return options


_REPAIR_SUFFIX = (
    " This is the one domain repair attempt: fix request.repair.reason "
    "and copy evidence only from request.repair.allowedEvidence."
)


class InterviewAgent:
    """Adapts four independently registered, typed sub-agents to the interview domain port."""

    def __init__(self, runtime: Runtime, options: InterviewAgentOptions | None = None):
        if runtime is None:
            raise ValueError("harness: interview runtime is required")
        if options is None:
            options = interview_agent_options_from_env()
        options = _normalize_options(options)
        self._runtime = runtime
        timeouts = {
            INTERVIEWER_AGENT_ID: options.interviewer_timeout,
            ASSESSOR_AGENT_ID: options.assessor_timeout,
            REPORTER_AGENT_ID: options.reporter_timeout,
            PLANNER_AGENT_ID: options.planner_timeout,
        }
        for agent_id, description, prompt, _default in _DEFAULT_AGENTS:
            if runtime.agent(agent_id) is not None:
                continue
            runtime.register(Agent(
                id=agent_id,
                description=description,
                system_prompt=prompt,
                timeout=timeouts[agent_id],
            ))

    async def generate_question(self, request: GenerateQuestionRequest) -> QuestionDraft:
        instruction = (
            "Generate the single best next interview question from the supplied typed request. "
            "Return only the structured QuestionDraft."
        )
        if request.repair is not None:
            instruction += _REPAIR_SUFFIX
        return await self._call(INTERVIEWER_AGENT_ID, instruction, request, QuestionDraft)

    async def assess_answer(self, request: AssessAnswerRequest) -> Assessment:
        instruction = (
            "Semantically assess this answer against the question and supplied anchors. "
            "Populate every rubric score, factualErrors, strengths, gaps, evidenceRefs and claimChecks. "
            "Never quote, paraphrase closely, or expose private knowledge reference content in narrative fields. "
            "Return only the structured Assessment."
        )
        if request.repair is not None:
            instruction += _REPAIR_SUFFIX
        return await self._call(ASSESSOR_AGENT_ID, instruction, request, Assessment)

    async def generate_report(self, request: GenerateReportRequest) -> ReportDraft:
        instruction = (
            "Synthesize only the committed interview evidence into the structured ReportDraft. "
            "Preserve uncertainty, do not invent facts, and do not reconstruct or expose private knowledge reference content."
        )
        if request.repair is not None:
            instruction += _REPAIR_SUFFIX
        return await self._call(REPORTER_AGENT_ID, instruction, request, ReportDraft)

    async def plan_coverage(self, request: PlanCoverageRequest) -> CoverageSelection:
        if not request.candidates:
            raise ValueError("harness: coverage planner needs at least one candidate")
        allowed: set[str] = set()
        has_alternative = False
        for candidate in request.candidates:
            candidate_id = candidate.coveragePointId.strip()
            if not candidate_id:
                raise ValueError("harness: coverage candidate id is required")
            if candidate_id in allowed:
                raise ValueError(f'harness: duplicate coverage candidate "{candidate_id}"')
            allowed.add(candidate_id)
            if candidate_id != request.currentCoveragePointId:
                has_alternative = True
        instruction = (
            "Select exactly one next coverage point from request.candidates using semantic assessment signals, "
            "per-candidate coverage counts, question-kind coverage and service priority. "
            "Do not use list order or round-robin. Return only the structured CoverageSelection."
        )
        result = await self._call(PLANNER_AGENT_ID, instruction, request, CoverageSelection)
        if result.coveragePointId not in allowed:
            raise ValueError(f'harness: coverage planner selected unknown candidate "{result.coveragePointId}"')
        if has_alternative and result.coveragePointId == request.currentCoveragePointId:
            raise ValueError(
                f'harness: coverage planner reselected current candidate "{result.coveragePointId}" despite available alternatives'
            )
        if not result.reason.strip():
            raise ValueError("harness: coverage planner returned an empty reason")
        if not result.signals:
            raise ValueError("harness: coverage planner returned no selection signals")
        for signal in result.signals:
            if not signal.strip():
                raise ValueError("harness: coverage planner returned an empty selection signal")
        return result

    async def _call(self, agent_id: str, instruction: str, request, out_model):
        context_text = json.dumps(request.model_dump(mode="json"), ensure_ascii=False)
        return await self._runtime.call_json(agent_id, instruction, context_text, out_model)
