"""Eval corpus/report schema mirroring Go evals/schema.go.

Provider-free and standalone: no dependency on the production interview service.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoverageRequirements(StrictModel):
    modes: list[str] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=list)


class Thresholds(StrictModel):
    maxDuplicateQuestionRate: float = 0.0
    minEvidenceValidityRate: float = 1.0
    minModeCoverageRate: float = 1.0
    minSeniorityCoverageRate: float = 1.0
    minModeSeniorityCoverageRate: float = 1.0
    minModeConformanceRate: float = 1.0
    maxPrivacyMarkerHits: int = 0


class Evidence(StrictModel):
    id: str
    kind: str
    label: str


class Question(StrictModel):
    id: str
    text: str
    kind: str
    difficulty: str
    evidenceRefIds: list[str] = Field(default_factory=list)


class PublicText(StrictModel):
    id: str
    text: str


class Case(StrictModel):
    id: str
    title: str
    mode: str
    seniority: str
    evidenceCatalog: list[Evidence] = Field(default_factory=list)
    privateMarkers: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    publicTexts: list[PublicText] = Field(default_factory=list)


class Corpus(StrictModel):
    schemaVersion: str
    corpusVersion: str
    description: str
    requiredCoverage: CoverageRequirements
    thresholds: Thresholds
    cases: list[Case] = Field(default_factory=list)


class Finding(StrictModel):
    code: str
    path: str
    message: str
    relatedId: str | None = None
    markerFingerprint: str | None = None


class CaseResult(StrictModel):
    id: str
    mode: str
    seniority: str
    passed: bool
    findings: list[Finding] = Field(default_factory=list)


class MetricFailure(StrictModel):
    metric: str
    comparator: str
    actual: float
    threshold: float
    message: str


class Metrics(StrictModel):
    caseCount: int = 0
    questionCount: int = 0
    publicTextCount: int = 0
    duplicateQuestionCount: int = 0
    duplicateQuestionRate: float = 0.0
    evidenceQuestionCount: int = 0
    validEvidenceQuestionCount: int = 0
    evidenceValidityRate: float = 0.0
    evidenceRefCount: int = 0
    validEvidenceRefCount: int = 0
    missingEvidenceQuestionCount: int = 0
    invalidEvidenceRefCount: int = 0
    evidenceKindMismatchCount: int = 0
    coveredModes: list[str] = Field(default_factory=list)
    modeCoverageRate: float = 0.0
    coveredSeniorities: list[str] = Field(default_factory=list)
    seniorityCoverageRate: float = 0.0
    coveredModeSeniorityPairs: list[str] = Field(default_factory=list)
    modeSeniorityCoverageRate: float = 0.0
    modeConformantCaseCount: int = 0
    modeConformanceRate: float = 0.0
    privacyFieldCount: int = 0
    privacyMarkerHitCount: int = 0
    privacyFieldsWithHits: int = 0
    privacyLeakFreeRate: float = 0.0


class Report(StrictModel):
    schemaVersion: str
    corpusVersion: str
    passed: bool
    thresholds: Thresholds
    metrics: Metrics
    cases: list[CaseResult] = Field(default_factory=list)
    failures: list[MetricFailure] = Field(default_factory=list)
