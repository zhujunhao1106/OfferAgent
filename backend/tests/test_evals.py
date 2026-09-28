"""P6a eval harness tests (mirrors Go evals/runner_test.go + cmd/offerpilot-eval/main_test.go)."""
import io
import json
import os
import shutil
import tempfile

import pytest

from evals import (
    Case,
    Corpus,
    CoverageRequirements,
    Evidence,
    PublicText,
    Question,
    Runner,
    Thresholds,
    decode_corpus,
    json_schema,
    load_default_corpus,
    normalize_comparable,
    validate_corpus,
)
from evals.cli import EXIT_FAILED, EXIT_INVALID, EXIT_PASSED, run


def test_default_corpus_passes_strict_gate():
    corpus = load_default_corpus()
    report = Runner(corpus.thresholds).run(corpus)
    assert report.passed is True
    assert report.metrics.caseCount == 30
    assert report.metrics.questionCount == 90
    assert report.metrics.modeSeniorityCoverageRate == 1
    assert report.metrics.evidenceValidityRate == 1
    assert report.metrics.duplicateQuestionCount == 0
    assert report.metrics.privacyMarkerHitCount == 0


def test_default_corpus_questions_unique_across_cases():
    corpus = load_default_corpus()
    seen = {}
    for case in corpus.cases:
        for question in case.questions:
            normalized = normalize_comparable(question.text)
            assert normalized not in seen, f"question {case.id}/{question.id} duplicates {seen[normalized]}"
            seen[normalized] = f"{case.id}/{question.id}"
    assert len(seen) == 90


def failing_corpus() -> Corpus:
    return Corpus(
        schemaVersion="1.0.0", corpusVersion="test-failing", description="Synthetic failing corpus",
        requiredCoverage=CoverageRequirements(
            modes=["knowledge", "projects", "mixed"], seniorities=["junior", "mid", "senior"],
        ),
        thresholds=Thresholds(
            maxDuplicateQuestionRate=0, minEvidenceValidityRate=1, minModeCoverageRate=1,
            minSeniorityCoverageRate=1, minModeSeniorityCoverageRate=1, minModeConformanceRate=1,
            maxPrivacyMarkerHits=0,
        ),
        cases=[Case(
            id="broken-case", title="Broken synthetic output", mode="knowledge", seniority="junior",
            evidenceCatalog=[Evidence(id="knowledge-1", kind="knowledge", label="Synthetic knowledge")],
            privateMarkers=["DO_NOT_EXPOSE_RAW_MARKER"],
            questions=[
                Question(id="q1", text="Explain the tradeoff.", kind="project", difficulty="easy", evidenceRefIds=["knowledge-1"]),
                Question(id="q2", text="EXPLAIN, THE TRADEOFF!", kind="knowledge", difficulty="medium", evidenceRefIds=["unknown"]),
                Question(id="q3", text="Which prerequisite is missing?", kind="prerequisite", difficulty="hard", evidenceRefIds=[]),
            ],
            publicTexts=[PublicText(id="leak", text="The hidden token is DO_NOT_EXPOSE_RAW_MARKER.")],
        )],
    )


def test_runner_rejects_duplicate_questions_across_cases():
    corpus = load_default_corpus()
    corpus.cases[1].questions[0].text = corpus.cases[0].questions[0].text
    report = Runner(corpus.thresholds).run(corpus)
    assert report.passed is False
    assert report.metrics.duplicateQuestionCount == 1
    findings = report.cases[1].findings
    assert findings and findings[0].code == "duplicate_question"
    assert findings[0].relatedId


def test_runner_reports_every_quality_metric_failure():
    report = Runner(failing_corpus().thresholds).run(failing_corpus())
    assert report.passed is False
    failed_metrics = {failure.metric for failure in report.failures}
    assert failed_metrics == {
        "duplicateQuestionRate", "evidenceValidityRate", "modeCoverageRate",
        "seniorityCoverageRate", "modeSeniorityCoverageRate", "modeConformanceRate",
        "privacyMarkerHitCount",
    }
    assert report.metrics.duplicateQuestionCount == 1
    assert report.metrics.invalidEvidenceRefCount > 0
    assert report.metrics.evidenceKindMismatchCount > 0


def test_corpus_validation_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        validate_corpus(Corpus(schemaVersion="", corpusVersion="", description="", requiredCoverage=CoverageRequirements(), thresholds=Thresholds(), cases=[]))

    with pytest.raises(ValueError):
        decode_corpus('{"schemaVersion":"1.0.0","unknown":true}')

    with pytest.raises(ValueError):
        decode_corpus("{} {}")

    with pytest.raises(ValueError):
        Runner(Thresholds(maxDuplicateQuestionRate=1.1))

    with pytest.raises(ValueError):
        Runner(Thresholds(minEvidenceValidityRate=float("nan")))

    corpus = failing_corpus()
    corpus.schemaVersion = "2.0.0"
    with pytest.raises(ValueError):
        validate_corpus(corpus)


def test_embedded_schema_is_json():
    schema = json.loads(json_schema())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_privacy_findings_do_not_reveal_raw_marker():
    corpus = failing_corpus()
    report = Runner(corpus.thresholds).run(corpus)
    encoded = json.dumps(report.model_dump(mode="json", exclude_none=True), ensure_ascii=False)
    assert "DO_NOT_EXPOSE_RAW_MARKER" not in encoded
    assert "markerFingerprint" in encoded


def test_report_ordering_is_deterministic():
    corpus = failing_corpus()
    corpus.cases[0].privateMarkers = ["ZETA_PRIVATE_MARKER", "ALPHA_PRIVATE_MARKER"]
    corpus.cases[0].publicTexts[0].text = "Both ALPHA_PRIVATE_MARKER and ZETA_PRIVATE_MARKER are private."
    runner = Runner(corpus.thresholds)
    baseline = None
    for _ in range(50):
        encoded = json.dumps(runner.run(corpus).model_dump(mode="json", exclude_none=True), ensure_ascii=False)
        if baseline is None:
            baseline = encoded
        else:
            assert encoded == baseline


def test_cli_run_default_corpus():
    stdout, stderr = io.StringIO(), io.StringIO()
    assert run(["-pretty=false"], stdout, stderr) == EXIT_PASSED
    report = json.loads(stdout.getvalue())
    assert report["passed"] is True
    assert report["corpusVersion"] == "v0.3.0-alpha.1"


@pytest.fixture
def temp_dir():
    directory = tempfile.mkdtemp()
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def test_cli_run_returns_one_when_thresholds_fail(temp_dir):
    import json as _json
    corpus = load_default_corpus()
    corpus.cases[0].questions[1].text = corpus.cases[0].questions[0].text
    path = os.path.join(temp_dir, "corpus.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_json.dumps(corpus.model_dump(mode="json"), ensure_ascii=False))

    stdout, stderr = io.StringIO(), io.StringIO()
    assert run(["-corpus", path, "-pretty=false"], stdout, stderr) == EXIT_FAILED
    report = json.loads(stdout.getvalue())
    assert report["passed"] is False
    assert report["metrics"]["duplicateQuestionCount"] == 1


def test_cli_run_returns_two_for_invalid_corpus(temp_dir):
    path = os.path.join(temp_dir, "invalid.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"schemaVersion":')
    stdout, stderr = io.StringIO(), io.StringIO()
    assert run(["-corpus", path], stdout, stderr) == EXIT_INVALID
    assert stdout.getvalue() == ""
    failure = json.loads(stderr.getvalue())
    assert failure["error"]


def test_cli_run_prints_schema():
    stdout, stderr = io.StringIO(), io.StringIO()
    assert run(["-schema"], stdout, stderr) == EXIT_PASSED
    assert json.loads(stdout.getvalue())["$schema"]
