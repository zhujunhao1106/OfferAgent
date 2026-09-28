"""Interview domain types mirroring Go interview/types.go.

Every model extends StrictModel (extra="forbid", matching Go's
DisallowUnknownFields on structured output). String enums are str-based Enum.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, model_validator

from ..llm.schema import StrictModel


class Action(str, Enum):
    START = "start"
    ANSWER = "answer"
    REPORT = "report"


class InterviewState(str, Enum):
    AWAITING_ANSWER = "awaiting_answer"
    COMPLETED = "completed"


class Focus(str, Enum):
    MIXED = "mixed"
    KNOWLEDGE = "knowledge"
    PROJECTS = "projects"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class FeedbackMode(str, Enum):
    IMMEDIATE = "immediate"
    DEFERRED = "deferred"


class InputMode(str, Enum):
    TEXT = "text"
    VOICE = "voice"


class SourceKind(str, Enum):
    JD = "jd"
    RESUME = "resume"
    KNOWLEDGE = "knowledge"


class QuestionKind(str, Enum):
    KNOWLEDGE = "knowledge"
    PROJECT = "project"
    BEHAVIORAL = "behavioral"
    FOLLOW_UP = "follow_up"
    PREREQUISITE = "prerequisite"


class PolicyAction(str, Enum):
    INITIAL = "initial"
    PREREQUISITE = "prerequisite"
    FOLLOW_UP = "follow_up"
    ADVANCE = "advance"
    COMPLETE = "complete"


class ClaimVerdict(str, Enum):
    SUPPORTED = "supported"
    UNVERIFIED = "unverified"
    CONTRADICTED = "contradicted"
    NOT_IN_MATERIAL = "not_in_material"


class MaterialInput(StrictModel):
    """Accepts either a plain JSON string or {name, text} (Go UnmarshalJSON)."""

    name: str = ""
    text: str

    @model_validator(mode="before")
    @classmethod
    def _coerce_string(cls, value):
        if isinstance(value, str):
            return {"text": value}
        return value


class MaterialsInput(StrictModel):
    jd: MaterialInput | None = None
    resume: MaterialInput | None = None


class InterviewConfig(StrictModel):
    focus: Focus = Focus.MIXED
    difficulty: Difficulty = Difficulty.MEDIUM
    questionCount: int = 6
    language: str = "zh-CN"
    feedbackMode: FeedbackMode = FeedbackMode.IMMEDIATE


class StartRequest(StrictModel):
    action: Action
    clientSessionId: str
    model: str = ""
    config: InterviewConfig = Field(default_factory=InterviewConfig)
    materials: MaterialsInput = Field(default_factory=MaterialsInput)


class StartResponse(StrictModel):
    interviewId: str
    state: InterviewState
    profile: Profile
    question: Question
    progress: Progress


class AnswerPayload(StrictModel):
    text: str
    inputMode: InputMode
    durationMs: int = 0


class AnswerRequest(StrictModel):
    action: Action
    interviewId: str
    questionId: str
    clientAnswerId: str
    answer: AnswerPayload


class AnswerFeedback(StrictModel):
    assessment: Assessment | None = None
    summary: str = ""
    focus: Focus
    deferred: bool = False


class AnswerResponse(StrictModel):
    interviewId: str
    state: InterviewState
    feedback: AnswerFeedback
    nextQuestion: Question | None = None
    progress: Progress
    reportReady: bool


class ReportRequest(StrictModel):
    action: Action
    interviewId: str


class ReportResponse(StrictModel):
    interviewId: str
    state: InterviewState
    report: Report


class EvidenceRef(StrictModel):
    sourceId: str
    kind: SourceKind
    anchorId: str
    locator: str
    quote: str


class SourceAnchor(StrictModel):
    id: str
    sourceId: str
    kind: SourceKind
    locator: str
    text: str


class SourceDocument(StrictModel):
    id: str
    kind: SourceKind
    name: str = ""
    content: str = Field(default="", exclude=True)


class SourceIndex(StrictModel):
    documents: dict[str, SourceDocument] = Field(default_factory=dict)
    anchors: dict[str, SourceAnchor] = Field(default_factory=dict)
    order: list[str] = Field(default_factory=list)


class ProfilePoint(StrictModel):
    id: str
    label: str
    evidenceRefs: list[EvidenceRef] = Field(default_factory=list)


class JDProfile(StrictModel):
    title: str = ""
    requirements: list[ProfilePoint] = Field(default_factory=list)
    responsibilities: list[ProfilePoint] = Field(default_factory=list)


class ResumeProfile(StrictModel):
    headline: str = ""
    skills: list[str] = Field(default_factory=list)
    projects: list[ProfilePoint] = Field(default_factory=list)


class CoveragePoint(StrictModel):
    id: str
    area: Focus
    label: str
    evidenceRefs: list[EvidenceRef] = Field(default_factory=list)


class CoverageCandidate(StrictModel):
    coveragePointId: str
    area: Focus
    label: str
    priority: int
    questionCount: int
    lastAskedTurn: int
    evidenceRefs: list[EvidenceRef] = Field(default_factory=list)


class Profile(StrictModel):
    jd: JDProfile = Field(default_factory=JDProfile)
    resume: ResumeProfile = Field(default_factory=ResumeProfile)
    coverage: list[CoveragePoint] = Field(default_factory=list)


class QuestionAdaptation(StrictModel):
    trigger: PolicyAction
    reason: str = ""
    basedOnQuestionId: str = ""
    followUpAxis: str = ""
    depth: int = 0


class Question(StrictModel):
    id: str
    rootId: str
    text: str
    kind: QuestionKind
    difficulty: Difficulty
    coveragePointId: str
    evidenceRefs: list[EvidenceRef] = Field(default_factory=list)
    adaptation: QuestionAdaptation


class Assessment(StrictModel):
    correctness: int
    depth: int
    specificity: int
    ownership: int
    metrics: int
    tradeoffs: int
    factualErrors: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    evidenceRefs: list[EvidenceRef] = Field(default_factory=list)
    claimChecks: list[ClaimCheck] = Field(default_factory=list)


class ClaimCheck(StrictModel):
    claim: str
    verdict: ClaimVerdict
    evidenceRefs: list[EvidenceRef] = Field(default_factory=list)


class PolicyDecision(StrictModel):
    action: PolicyAction
    reason: str
    followUpAxis: str = ""
    difficulty: Difficulty
    coveragePointId: str
    rootId: str
    followUpDepth: int


class Progress(StrictModel):
    answered: int
    total: int
    current: int
    followUpDepth: int


class AnswerRecord(StrictModel):
    question: Question
    answer: AnswerPayload
    assessment: Assessment
    decision: PolicyDecision
    answeredAt: datetime


class AuditTurn(StrictModel):
    questionId: str
    rootId: str
    coveragePointId: str
    evidenceRefs: list[EvidenceRef] = Field(default_factory=list)
    inputMode: InputMode
    durationMs: int = 0
    answeredAt: datetime


class ReportAudit(StrictModel):
    clientSessionId: str
    startedAt: datetime
    completedAt: datetime | None = None
    generatedAt: datetime
    turns: list[AuditTurn] = Field(default_factory=list)


class Report(StrictModel):
    overallScore: int
    summary: str
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    evidenceRefs: list[EvidenceRef] = Field(default_factory=list)
    profile: Profile
    turns: list[AnswerRecord] = Field(default_factory=list)
    audit: ReportAudit


class InterviewSession(StrictModel):
    id: str
    clientSessionId: str
    model: str = ""
    config: InterviewConfig
    state: InterviewState
    profile: Profile
    sources: SourceIndex = Field(default_factory=SourceIndex)
    currentQuestion: Question | None = None
    answers: list[AnswerRecord] = Field(default_factory=list)
    coverageCursor: int = 0
    report: Report | None = None
    startedAt: datetime
    completedAt: datetime | None = None
    version: int


class QuestionDraft(StrictModel):
    text: str
    evidenceRefs: list[EvidenceRef] = Field(default_factory=list)


class ReportDraft(StrictModel):
    summary: str
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    evidenceRefs: list[EvidenceRef] = Field(default_factory=list)


class PlanCoverageRequest(StrictModel):
    config: InterviewConfig
    currentCoveragePointId: str
    previousQuestion: Question
    previousAssessment: Assessment
    candidates: list[CoverageCandidate] = Field(default_factory=list)
    questionKindCounts: dict[QuestionKind, int] = Field(default_factory=dict)
    history: list[AnswerRecord] = Field(default_factory=list)
    remainingQuestions: int = 0


class CoverageSelection(StrictModel):
    coveragePointId: str
    reason: str
    signals: list[str] = Field(default_factory=list)


class RepairInstruction(StrictModel):
    reason: str
    allowedEvidence: list[EvidenceRef] = Field(default_factory=list)


class GenerateQuestionRequest(StrictModel):
    profile: Profile
    decision: PolicyDecision
    anchors: list[SourceAnchor] = Field(default_factory=list)
    history: list[AnswerRecord] = Field(default_factory=list)
    repair: RepairInstruction | None = None


class AssessAnswerRequest(StrictModel):
    question: Question
    answer: AnswerPayload
    anchors: list[SourceAnchor] = Field(default_factory=list)
    history: list[AnswerRecord] = Field(default_factory=list)
    repair: RepairInstruction | None = None


class GenerateReportRequest(StrictModel):
    profile: Profile
    answers: list[AnswerRecord] = Field(default_factory=list)
    anchors: list[SourceAnchor] = Field(default_factory=list)
    repair: RepairInstruction | None = None


class KnowledgeQuery(StrictModel):
    model: str = ""
    focus: Focus = Focus.MIXED
    jd: str = ""
    resume: str = ""
    coveragePointId: str = ""
    objective: str = ""
    question: str = ""
    previousGaps: list[str] = Field(default_factory=list)


class KnowledgeDocument(StrictModel):
    id: str
    title: str
    content: str
