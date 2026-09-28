"""Offline eval quality gate (mirrors Go internal/evals + cmd/offerpilot-eval)."""
from .corpus import decode_corpus, json_schema, load_corpus_file, load_default_corpus
from .runner import (
    CORPUS_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    Runner,
    normalize_comparable,
    validate_corpus,
)
from .schema import (
    Case,
    CaseResult,
    Corpus,
    CoverageRequirements,
    Evidence,
    Finding,
    MetricFailure,
    Metrics,
    PublicText,
    Question,
    Report,
    Thresholds,
)

__all__ = [
    "decode_corpus", "json_schema", "load_corpus_file", "load_default_corpus",
    "CORPUS_SCHEMA_VERSION", "REPORT_SCHEMA_VERSION", "Runner",
    "normalize_comparable", "validate_corpus",
    "Case", "CaseResult", "Corpus", "CoverageRequirements", "Evidence", "Finding",
    "MetricFailure", "Metrics", "PublicText", "Question", "Report", "Thresholds",
]
