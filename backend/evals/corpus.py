"""Corpus loading mirroring Go evals/corpus.go."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .schema import Corpus

DEFAULT_CORPUS_PATH = "corpus/v0.3.0-alpha.1.json"

_PACKAGE_DIR = Path(__file__).parent


def load_default_corpus() -> Corpus:
    data = (_PACKAGE_DIR / DEFAULT_CORPUS_PATH).read_bytes()
    return decode_corpus(data)


def load_corpus_file(path: str) -> Corpus:
    return decode_corpus(Path(path).read_bytes())


def decode_corpus(raw: bytes | str) -> Corpus:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"evals: decode corpus: {exc}") from exc
    try:
        return Corpus.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"evals: decode corpus: {exc}") from exc


def json_schema() -> bytes:
    return (_PACKAGE_DIR / "schema" / "corpus.schema.json").read_bytes()
