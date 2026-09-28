"""Source index helpers + knowledge privacy sanitization mirroring Go interview/sources.go."""
from __future__ import annotations

import unicodedata

from .types import (
    CoveragePoint,
    EvidenceRef,
    Focus,
    SourceAnchor,
    SourceDocument,
    SourceIndex,
    SourceKind,
)

MIN_PRIVATE_REFERENCE_FRAGMENT_RUNES = 16

KNOWLEDGE_QUESTION_PREFIXES = ["问题：", "问题:", "question:", "question："]

KNOWLEDGE_REFERENCE_MARKERS = [
    "参考内容：", "参考内容:",
    "参考答案：", "参考答案:",
    "reference content:", "reference content：",
    "reference answer:", "reference answer：",
]

KNOWLEDGE_SOURCE_MARKERS = ["\n来源：", "\n来源:", "\nsource:", "\nsource："]


def public_evidence_quote(ref: EvidenceRef) -> str:
    if ref.kind != SourceKind.KNOWLEDGE:
        return ref.quote
    return public_knowledge_question(ref.quote)


def public_generated_text(value: str, refs: list[EvidenceRef]) -> str:
    value = value.strip()
    if not value or first_folded_marker(value, KNOWLEDGE_REFERENCE_MARKERS) >= 0:
        return ""
    normalized_value = normalize_question_guard_text(value)
    for ref in refs:
        if ref.kind != SourceKind.KNOWLEDGE:
            continue
        reference = knowledge_reference_text(ref.quote)
        if not reference:
            continue
        normalized_reference = normalize_question_guard_text(reference)
        for start in range(len(normalized_reference) - MIN_PRIVATE_REFERENCE_FRAGMENT_RUNES + 1):
            fragment = normalized_reference[start:start + MIN_PRIVATE_REFERENCE_FRAGMENT_RUNES]
            if fragment in normalized_value:
                return ""
    return value


def public_knowledge_question(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    for line in normalized.split("\n"):
        line = line.strip()
        question, ok = trim_folded_prefix(line, KNOWLEDGE_QUESTION_PREFIXES)
        if ok and question:
            position = first_folded_marker(question, KNOWLEDGE_REFERENCE_MARKERS)
            if position >= 0:
                question = question[:position].strip()
            if not question:
                continue
            return "问题：" + question
    position = first_folded_marker(normalized, KNOWLEDGE_REFERENCE_MARKERS)
    if position >= 0:
        public = normalized[:position].strip()
        if public:
            return public
    return "知识题"


def knowledge_question_label(value: str) -> str:
    public = public_knowledge_question(value)
    question, ok = trim_folded_prefix(public.strip(), KNOWLEDGE_QUESTION_PREFIXES)
    if ok and question:
        return question
    return public


def knowledge_reference_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    position = first_folded_marker(normalized, KNOWLEDGE_REFERENCE_MARKERS)
    if position < 0:
        return ""
    reference = normalized[position:]
    _prefix, body, ok = split_folded_prefix(reference, KNOWLEDGE_REFERENCE_MARKERS)
    if ok:
        reference = body
    end = first_folded_marker(reference, KNOWLEDGE_SOURCE_MARKERS)
    if end >= 0:
        reference = reference[:end]
    return reference.strip()


def trim_folded_prefix(value: str, prefixes: list[str]) -> tuple[str, bool]:
    _prefix, remainder, ok = split_folded_prefix(value, prefixes)
    return remainder.strip(), ok


def split_folded_prefix(value: str, prefixes: list[str]) -> tuple[str, str, bool]:
    lower = value.lower()
    for prefix in prefixes:
        if lower.startswith(prefix.lower()):
            return value[:len(prefix)], value[len(prefix):], True
    return "", value, False


def first_folded_marker(value: str, markers: list[str]) -> int:
    lower = value.lower()
    position = -1
    for marker in markers:
        candidate = lower.find(marker.lower())
        if candidate >= 0 and (position < 0 or candidate < position):
            position = candidate
    return position


def add_knowledge_document(index: SourceIndex, id: str, name: str, content: str) -> None:
    content = content.strip()
    index.documents[id] = SourceDocument(id=id, kind=SourceKind.KNOWLEDGE, name=name.strip(), content=content)
    anchor_id = id + ":block"
    index.anchors[anchor_id] = SourceAnchor(
        id=anchor_id, sourceId=id, kind=SourceKind.KNOWLEDGE, locator="question-block", text=content,
    )
    index.order.append(anchor_id)


def merge_knowledge_documents(index: SourceIndex | None, documents: list, limit: int) -> list[EvidenceRef]:
    if index is None:
        return []
    refs: list[EvidenceRef] = []
    for position, document in enumerate(documents):
        if limit > 0 and len(refs) >= limit:
            break
        content = document.content.strip()
        if not content:
            continue
        name = document.title.strip()
        if not name:
            name = document.id.strip()
        base_id = "knowledge:" + document.id.strip()
        if not document.id.strip():
            base_id = f"knowledge:{position + 1:03d}"
        source_id = base_id
        suffix = 2
        while True:
            existing = index.documents.get(source_id)
            if existing is None:
                add_knowledge_document(index, source_id, name, content)
                break
            if existing.kind == SourceKind.KNOWLEDGE and existing.content.strip() == content:
                break
            source_id = f"{base_id}:{suffix}"
            suffix += 1
        anchor = index.anchors.get(source_id + ":block")
        if anchor is not None:
            refs.append(evidence_from_anchor(anchor))
    return refs


def anchors_for_evidence(index: SourceIndex, refs: list[EvidenceRef]) -> list[SourceAnchor]:
    anchors: list[SourceAnchor] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.anchorId in seen:
            continue
        anchor = index.anchors.get(ref.anchorId)
        if anchor is not None:
            seen.add(ref.anchorId)
            anchors.append(anchor)
    return anchors


def all_anchors(index: SourceIndex) -> list[SourceAnchor]:
    return [index.anchors[id] for id in index.order if id in index.anchors]


def evidence_from_anchor(anchor: SourceAnchor) -> EvidenceRef:
    return EvidenceRef(
        sourceId=anchor.sourceId, kind=anchor.kind, anchorId=anchor.id,
        locator=anchor.locator, quote=anchor.text,
    )


def all_evidence(index: SourceIndex) -> list[EvidenceRef]:
    return [evidence_from_anchor(anchor) for anchor in all_anchors(index)]


def validate_evidence_refs(
    index: SourceIndex,
    refs: list[EvidenceRef],
    allowed: set[str] | None,
    require: bool,
) -> None:
    if require and not refs:
        raise ValueError("at least one evidence reference is required")
    for ref in refs:
        anchor = index.anchors.get(ref.anchorId)
        if anchor is None:
            raise ValueError(f'unknown anchor "{ref.anchorId}"')
        if allowed is not None and ref.anchorId not in allowed:
            raise ValueError(f'anchor "{ref.anchorId}" is outside the allowed evidence set')
        if ref.sourceId != anchor.sourceId or ref.kind != anchor.kind or ref.locator != anchor.locator:
            raise ValueError(f'metadata does not match anchor "{ref.anchorId}"')
        if normalize_evidence_text(ref.quote) != normalize_evidence_text(anchor.text):
            raise ValueError(f'quote does not match anchor "{ref.anchorId}"')


def canonical_evidence(index: SourceIndex, refs: list[EvidenceRef]) -> list[EvidenceRef]:
    result: list[EvidenceRef] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.anchorId in seen:
            continue
        if ref.anchorId in index.anchors:
            seen.add(ref.anchorId)
            result.append(evidence_from_anchor(index.anchors[ref.anchorId]))
    return result


def normalize_evidence_text(value: str) -> str:
    return " ".join(value.lower().split())


def public_anchor_label(anchor: SourceAnchor) -> str:
    if anchor.kind == SourceKind.KNOWLEDGE:
        return concise(knowledge_question_label(anchor.text), 120)
    return concise(anchor.text, 120)


def interleave_coverage(first: list[CoveragePoint], second: list[CoveragePoint]) -> list[CoveragePoint]:
    result: list[CoveragePoint] = []
    for i in range(max(len(first), len(second))):
        if i < len(first):
            result.append(first[i])
        if i < len(second):
            result.append(second[i])
    return result


def normalize_question_guard_text(value: str) -> str:
    out: list[str] = []
    for ch in value:
        category = unicodedata.category(ch)
        if category[0] == "L":
            out.append(ch.lower())
        elif category[0] == "N":
            out.append(ch)
    return "".join(out)


def contains_any(value: str, *candidates: str) -> bool:
    for candidate in candidates:
        if candidate in value:
            return True
    return False


def concise(value: str, max_runes: int) -> str:
    runes = value.strip()
    if len(runes) <= max_runes:
        return runes
    return runes[:max_runes] + "..."


def min_int(a: int, b: int) -> int:
    return a if a < b else b
