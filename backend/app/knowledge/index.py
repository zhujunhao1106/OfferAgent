"""BM25 index mirroring Go index.go."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .tokenize import normalize_for_phrase, tokenize, unique


@dataclass
class Entry:
    id: str
    source: str
    title: str
    question: str
    excerpt: str
    score: float = 0.0
    body: str = field(default="", repr=False)

    def public(self, score: float = 0.0) -> "Entry":
        return Entry(
            id=self.id, source=self.source, title=self.title,
            question=self.question, excerpt=self.excerpt, score=score,
        )


@dataclass
class _Document:
    entry: Entry
    terms: dict[str, int]
    length: int


class Index:
    """Immutable after construction; safe for concurrent searches."""

    def __init__(self, documents: list[_Document], df: dict[str, int], avg_len: float):
        self._documents = documents
        self._df = df
        self._avg_len = avg_len

    def len(self) -> int:
        return len(self._documents)

    def entries(self) -> list[Entry]:
        return [doc.entry.public(0.0) for doc in self._documents]

    def search(self, query: str, limit: int) -> list[Entry]:
        if not self._documents or not query.strip():
            return []
        if limit <= 0:
            limit = 5
        query_tokens = unique(tokenize(query))
        if not query_tokens:
            return []
        normalized_query = normalize_for_phrase(query)
        has_strong = any(len(token) > 1 for token in query_tokens)
        results: list[tuple[Entry, float]] = []
        for doc in self._documents:
            if has_strong and not _matches_strong_term(doc, query_tokens):
                continue
            score = self._bm25(doc, query_tokens)
            if score == 0:
                continue
            normalized_question = normalize_for_phrase(doc.entry.question)
            normalized_body = normalize_for_phrase(doc.entry.body)
            if len(normalized_query) >= 2:
                if normalized_query in normalized_question:
                    score += 6
                elif normalized_query in normalized_body:
                    score += 2
            results.append((doc.entry, score))
        results.sort(key=lambda item: (-item[1], item[0].id))
        results = results[:limit]
        return [entry.public(_round_6(score)) for entry, score in results]

    def _bm25(self, doc: _Document, query_tokens: list[str]) -> float:
        k1 = 1.35
        b = 0.72
        document_count = float(len(self._documents))
        score = 0.0
        for token in query_tokens:
            frequency = doc.terms.get(token, 0)
            if frequency == 0:
                continue
            document_frequency = float(self._df[token])
            idf = math.log(1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5))
            tf = float(frequency)
            normalizer = tf + k1 * (1 - b + b * float(doc.length) / self._avg_len)
            score += idf * (tf * (k1 + 1) / normalizer)
        return score


def _matches_strong_term(doc: _Document, query_tokens: list[str]) -> bool:
    for token in query_tokens:
        if len(token) > 1 and doc.terms.get(token, 0) > 0:
            return True
    return False


def _round_6(value: float) -> float:
    # Go math.Round (half away from zero); scores are non-negative so floor(x+0.5).
    return math.floor(value * 1e6 + 0.5) / 1e6


def load(root: str) -> Index:
    from .markdown import parse_markdown

    root = root.strip()
    if not root:
        raise ValueError("knowledge: root directory is required")
    root_path = Path(root)
    if not root_path.exists():
        raise ValueError(f"knowledge: stat root: {root}")
    if not root_path.is_dir():
        raise ValueError(f"knowledge: {root} is not a directory")
    entries: list[Entry] = []
    paths = sorted(
        p for p in root_path.rglob("*")
        if p.is_file() and p.suffix.lower() == ".md"
    )
    for path in paths:
        data = path.read_text(encoding="utf-8")
        relative = path.relative_to(root_path).as_posix()
        entries.extend(parse_markdown(relative, data))
    return build_index(entries)


def new_index(entries: list[Entry]) -> Index:
    copied = [
        Entry(
            id=e.id, source=e.source, title=e.title, question=e.question,
            excerpt=e.excerpt, score=0.0, body=e.body or e.excerpt,
        )
        for e in entries
    ]
    return build_index(copied)


def build_index(entries: list[Entry]) -> Index:
    df: dict[str, int] = {}
    documents: list[_Document] = []
    total_length = 0
    for entry in entries:
        weighted = (
            entry.title + " " + entry.title + " " +
            entry.question + " " + entry.question + " " + entry.question + " " +
            entry.body
        )
        tokens = tokenize(weighted)
        terms: dict[str, int] = {}
        for token in tokens:
            terms[token] = terms.get(token, 0) + 1
        for token in terms:
            df[token] = df.get(token, 0) + 1
        total_length += len(tokens)
        documents.append(_Document(entry=entry, terms=terms, length=len(tokens)))
    avg_len = total_length / len(documents) if documents else 0.0
    if avg_len == 0:
        avg_len = 1.0
    return Index(documents, df, avg_len)
