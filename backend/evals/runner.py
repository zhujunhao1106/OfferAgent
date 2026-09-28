"""Deterministic quality-gate runner mirroring Go evals/runner.go."""
from __future__ import annotations

import hashlib
import unicodedata

from .schema import (
    Case,
    CaseResult,
    Corpus,
    Finding,
    MetricFailure,
    Metrics,
    Report,
    Thresholds,
)

CORPUS_SCHEMA_VERSION = "1.0.0"
REPORT_SCHEMA_VERSION = "1.0.0"

VALID_MODES = {"knowledge", "projects", "mixed"}
VALID_SENIORITIES = {"junior", "mid", "senior"}
VALID_KINDS = {"knowledge", "project", "behavioral", "follow_up", "prerequisite"}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}
VALID_EVIDENCE_KINDS = {"jd", "resume", "knowledge"}
STANDARD_PRIVACY_MARKERS = ["参考内容", "参考答案", "reference content", "reference answer"]


class Runner:
    def __init__(self, thresholds: Thresholds):
        validate_thresholds(thresholds)
        self._thresholds = thresholds

    def run(self, corpus: Corpus) -> Report:
        validate_corpus(corpus)

        metrics = Metrics()
        metrics.caseCount = len(corpus.cases)
        report = Report(
            schemaVersion=REPORT_SCHEMA_VERSION,
            corpusVersion=corpus.corpusVersion,
            passed=False,
            thresholds=self._thresholds,
            metrics=metrics,
        )
        covered_modes: set[str] = set()
        covered_seniorities: set[str] = set()
        covered_pairs: set[str] = set()
        seen_questions: dict[str, str] = {}

        for case in corpus.cases:
            case_result = evaluate_case(case, metrics, seen_questions)
            report.cases.append(case_result)
            covered_modes.add(case.mode)
            covered_seniorities.add(case.seniority)
            covered_pairs.add(pair_key(case.mode, case.seniority))

        metrics.duplicateQuestionRate = ratio(metrics.duplicateQuestionCount, metrics.questionCount)
        metrics.evidenceValidityRate = ratio(metrics.validEvidenceQuestionCount, metrics.evidenceQuestionCount)
        metrics.coveredModes, metrics.modeCoverageRate = coverage(corpus.requiredCoverage.modes, covered_modes)
        metrics.coveredSeniorities, metrics.seniorityCoverageRate = coverage(corpus.requiredCoverage.seniorities, covered_seniorities)
        required_pairs = [
            pair_key(mode, seniority)
            for mode in corpus.requiredCoverage.modes
            for seniority in corpus.requiredCoverage.seniorities
        ]
        metrics.coveredModeSeniorityPairs, metrics.modeSeniorityCoverageRate = coverage(required_pairs, covered_pairs)
        metrics.modeConformanceRate = ratio(metrics.modeConformantCaseCount, metrics.caseCount)
        metrics.privacyLeakFreeRate = ratio(metrics.privacyFieldCount - metrics.privacyFieldsWithHits, metrics.privacyFieldCount)

        report.failures = threshold_failures(metrics, self._thresholds)
        report.passed = len(report.failures) == 0
        return report


def validate_corpus(corpus: Corpus) -> None:
    if corpus.schemaVersion != CORPUS_SCHEMA_VERSION:
        raise ValueError(f'evals: schemaVersion must be "{CORPUS_SCHEMA_VERSION}"')
    if not corpus.corpusVersion.strip():
        raise ValueError("evals: corpusVersion is required")
    if not corpus.description.strip():
        raise ValueError("evals: description is required")
    validate_thresholds(corpus.thresholds)
    validate_required_values("requiredCoverage.modes", corpus.requiredCoverage.modes, VALID_MODES)
    validate_required_values("requiredCoverage.seniorities", corpus.requiredCoverage.seniorities, VALID_SENIORITIES)
    if not corpus.cases:
        raise ValueError("evals: at least one case is required")

    case_ids: set[str] = set()
    for case_index, case in enumerate(corpus.cases):
        path = f"cases[{case_index}]"
        require_unique_id(path + ".id", case.id, case_ids)
        if not case.title.strip():
            raise ValueError(f"evals: {path}.title is required")
        if case.mode not in VALID_MODES:
            raise ValueError(f'evals: {path}.mode "{case.mode}" is invalid')
        if case.seniority not in VALID_SENIORITIES:
            raise ValueError(f'evals: {path}.seniority "{case.seniority}" is invalid')
        if not case.evidenceCatalog:
            raise ValueError(f"evals: {path}.evidenceCatalog must not be empty")
        evidence_ids: set[str] = set()
        for evidence_index, evidence in enumerate(case.evidenceCatalog):
            evidence_path = f"{path}.evidenceCatalog[{evidence_index}]"
            require_unique_id(evidence_path + ".id", evidence.id, evidence_ids)
            if evidence.kind not in VALID_EVIDENCE_KINDS:
                raise ValueError(f'evals: {evidence_path}.kind "{evidence.kind}" is invalid')
            if not evidence.label.strip():
                raise ValueError(f"evals: {evidence_path}.label is required")
        if not case.privateMarkers:
            raise ValueError(f"evals: {path}.privateMarkers must not be empty")
        for marker_index, marker in enumerate(case.privateMarkers):
            if not normalize_comparable(marker):
                raise ValueError(f"evals: {path}.privateMarkers[{marker_index}] is empty after normalization")
        if not case.questions:
            raise ValueError(f"evals: {path}.questions must not be empty")
        question_ids: set[str] = set()
        for question_index, question in enumerate(case.questions):
            question_path = f"{path}.questions[{question_index}]"
            require_unique_id(question_path + ".id", question.id, question_ids)
            if not normalize_comparable(question.text):
                raise ValueError(f"evals: {question_path}.text is empty after normalization")
            if question.kind not in VALID_KINDS:
                raise ValueError(f'evals: {question_path}.kind "{question.kind}" is invalid')
            if question.difficulty not in VALID_DIFFICULTIES:
                raise ValueError(f'evals: {question_path}.difficulty "{question.difficulty}" is invalid')
        public_text_ids: set[str] = set()
        for text_index, public_text in enumerate(case.publicTexts):
            text_path = f"{path}.publicTexts[{text_index}]"
            require_unique_id(text_path + ".id", public_text.id, public_text_ids)
            if not public_text.text.strip():
                raise ValueError(f"evals: {text_path}.text is required")


def evaluate_case(case: Case, metrics: Metrics, seen_questions: dict[str, str]) -> CaseResult:
    result = CaseResult(id=case.id, mode=case.mode, seniority=case.seniority, passed=False)
    evidence = {item.id: item for item in case.evidenceCatalog}

    for question_index, question in enumerate(case.questions):
        metrics.questionCount += 1
        metrics.evidenceQuestionCount += 1
        question_path = f"questions[{question_index}]"
        normalized = normalize_comparable(question.text)
        previous_id = seen_questions.get(normalized)
        if previous_id is not None:
            metrics.duplicateQuestionCount += 1
            result.findings.append(Finding(
                code="duplicate_question", path=question_path + ".text",
                message="question duplicates an earlier normalized question in the corpus",
                relatedId=previous_id,
            ))
        else:
            seen_questions[normalized] = f"{case.id}/{question.id}"

        valid_question_evidence = True
        if not question.evidenceRefIds:
            valid_question_evidence = False
            metrics.missingEvidenceQuestionCount += 1
            result.findings.append(Finding(
                code="missing_evidence", path=question_path + ".evidenceRefIds",
                message="question has no evidence reference",
            ))
        seen_refs: set[str] = set()
        resolved_kinds: set[str] = set()
        for ref_index, ref_id in enumerate(question.evidenceRefIds):
            metrics.evidenceRefCount += 1
            ref_path = f"{question_path}.evidenceRefIds[{ref_index}]"
            if ref_id in seen_refs:
                valid_question_evidence = False
                metrics.invalidEvidenceRefCount += 1
                result.findings.append(Finding(
                    code="duplicate_evidence_ref", path=ref_path,
                    message="evidence reference is repeated within the question", relatedId=ref_id,
                ))
                continue
            seen_refs.add(ref_id)
            item = evidence.get(ref_id)
            if item is None:
                valid_question_evidence = False
                metrics.invalidEvidenceRefCount += 1
                result.findings.append(Finding(
                    code="unknown_evidence_ref", path=ref_path,
                    message="evidence reference is absent from the case catalog", relatedId=ref_id,
                ))
                continue
            metrics.validEvidenceRefCount += 1
            resolved_kinds.add(item.kind)
        if question.evidenceRefIds and not evidence_kind_compatible(question.kind, resolved_kinds):
            valid_question_evidence = False
            metrics.evidenceKindMismatchCount += 1
            result.findings.append(Finding(
                code="evidence_kind_mismatch", path=question_path + ".evidenceRefIds",
                message="resolved evidence kinds do not support the question kind",
            ))
        if valid_question_evidence:
            metrics.validEvidenceQuestionCount += 1

    if mode_conformant(case):
        metrics.modeConformantCaseCount += 1
    else:
        result.findings.append(Finding(
            code="mode_conformance", path="questions",
            message="question kinds do not cover the case mode",
        ))

    markers = unique_normalized_markers(list(STANDARD_PRIVACY_MARKERS) + list(case.privateMarkers))
    privacy_fields = [(f"questions[{index}].text", q.text) for index, q in enumerate(case.questions)]
    privacy_fields += [(f"publicTexts[{index}].text", t.text) for index, t in enumerate(case.publicTexts)]
    metrics.publicTextCount += len(case.publicTexts)
    metrics.privacyFieldCount += len(privacy_fields)
    for path, text in privacy_fields:
        normalized = normalize_comparable(text)
        field_has_hit = False
        for marker in markers:
            if marker["normalized"] not in normalized:
                continue
            field_has_hit = True
            metrics.privacyMarkerHitCount += 1
            result.findings.append(Finding(
                code="privacy_marker", path=path,
                message="public text contains a normalized private marker",
                markerFingerprint=marker["fingerprint"],
            ))
        if field_has_hit:
            metrics.privacyFieldsWithHits += 1

    result.passed = len(result.findings) == 0
    return result


def threshold_failures(metrics: Metrics, thresholds: Thresholds) -> list[MetricFailure]:
    failures: list[MetricFailure] = []

    def check_max(metric: str, actual: float, threshold: float) -> None:
        if actual > threshold:
            failures.append(MetricFailure(
                metric=metric, comparator="<=", actual=actual, threshold=threshold,
                message="metric exceeds maximum threshold",
            ))

    def check_min(metric: str, actual: float, threshold: float) -> None:
        if actual < threshold:
            failures.append(MetricFailure(
                metric=metric, comparator=">=", actual=actual, threshold=threshold,
                message="metric is below minimum threshold",
            ))

    check_max("duplicateQuestionRate", metrics.duplicateQuestionRate, thresholds.maxDuplicateQuestionRate)
    check_min("evidenceValidityRate", metrics.evidenceValidityRate, thresholds.minEvidenceValidityRate)
    check_min("modeCoverageRate", metrics.modeCoverageRate, thresholds.minModeCoverageRate)
    check_min("seniorityCoverageRate", metrics.seniorityCoverageRate, thresholds.minSeniorityCoverageRate)
    check_min("modeSeniorityCoverageRate", metrics.modeSeniorityCoverageRate, thresholds.minModeSeniorityCoverageRate)
    check_min("modeConformanceRate", metrics.modeConformanceRate, thresholds.minModeConformanceRate)
    check_max("privacyMarkerHitCount", float(metrics.privacyMarkerHitCount), float(thresholds.maxPrivacyMarkerHits))
    return failures


def validate_thresholds(thresholds: Thresholds) -> None:
    for name, value in (
        ("maxDuplicateQuestionRate", thresholds.maxDuplicateQuestionRate),
        ("minEvidenceValidityRate", thresholds.minEvidenceValidityRate),
        ("minModeCoverageRate", thresholds.minModeCoverageRate),
        ("minSeniorityCoverageRate", thresholds.minSeniorityCoverageRate),
        ("minModeSeniorityCoverageRate", thresholds.minModeSeniorityCoverageRate),
        ("minModeConformanceRate", thresholds.minModeConformanceRate),
    ):
        if not (0 <= value <= 1):
            raise ValueError(f"evals: threshold {name} must be between 0 and 1")
    if thresholds.maxPrivacyMarkerHits < 0:
        raise ValueError("evals: threshold maxPrivacyMarkerHits must be non-negative")


def validate_required_values(path: str, values: list[str], allowed: set[str]) -> None:
    if not values:
        raise ValueError(f"evals: {path} must not be empty")
    seen: set[str] = set()
    for index, value in enumerate(values):
        if value not in allowed:
            raise ValueError(f'evals: {path}[{index}] "{value}" is invalid')
        if value in seen:
            raise ValueError(f'evals: {path} contains duplicate value "{value}"')
        seen.add(value)


def require_unique_id(path: str, value: str, seen: set[str]) -> None:
    value = value.strip()
    if not value:
        raise ValueError(f"evals: {path} is required")
    if value in seen:
        raise ValueError(f'evals: {path} "{value}" is duplicated')
    seen.add(value)


def evidence_kind_compatible(question_kind: str, kinds: set[str]) -> bool:
    if question_kind in ("knowledge", "prerequisite"):
        return "knowledge" in kinds
    if question_kind in ("project", "behavioral"):
        return "resume" in kinds or "jd" in kinds
    if question_kind == "follow_up":
        return len(kinds) > 0
    return False


def mode_conformant(case: Case) -> bool:
    has_knowledge = False
    has_project = False
    for question in case.questions:
        if question.kind in ("knowledge", "prerequisite"):
            has_knowledge = True
        elif question.kind in ("project", "behavioral"):
            has_project = True
    if case.mode == "knowledge":
        return has_knowledge and not has_project
    if case.mode == "projects":
        return has_project and not has_knowledge
    if case.mode == "mixed":
        return has_knowledge and has_project
    return False


def coverage(required: list[str], covered: set[str]) -> tuple[list[str], float]:
    result = [value for value in required if value in covered]
    return result, ratio(len(result), len(required))


def pair_key(mode: str, seniority: str) -> str:
    return f"{mode}:{seniority}"


def ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def normalize_comparable(value: str) -> str:
    out: list[str] = []
    for ch in value:
        category = unicodedata.category(ch)
        if category[0] == "L":
            out.append(ch.lower())
        elif category[0] == "N":
            out.append(ch)
    return "".join(out)


def unique_normalized_markers(markers: list[str]) -> list[dict]:
    fingerprints: dict[str, str] = {}
    for marker in markers:
        normalized = normalize_comparable(marker)
        if not normalized or normalized in fingerprints:
            continue
        fingerprints[normalized] = hashlib.sha256(marker.encode("utf-8")).hexdigest()[:12]
    return [
        {"normalized": normalized, "fingerprint": fingerprints[normalized]}
        for normalized in sorted(fingerprints)
    ]
