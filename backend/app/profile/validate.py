"""Grounded profile validation mirroring Go profile/validate.go."""
from __future__ import annotations

from .types import (
    AgentRequest,
    EvidenceRef,
    Fact,
    Profile,
    SourceAnchor,
    SourceDocument,
    SourceKind,
)


class UngroundedFactError(Exception):
    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"profile: ungrounded fact at {path}: {reason}")


def normalize_grounding(value: str) -> str:
    return " ".join(value.lower().split())


def _errors_at(path: str, reason: str) -> UngroundedFactError:
    return UngroundedFactError(path, reason)


def validate(request: AgentRequest, result: Profile) -> None:
    documents: dict[str, SourceDocument] = {}
    for document in request.documents:
        if not document.id.strip():
            raise _errors_at("request.documents", "empty document ID")
        if document.kind not in (SourceKind.JD, SourceKind.RESUME):
            raise _errors_at("request.documents", f'unsupported source kind "{document.kind}"')
        if document.id in documents:
            raise _errors_at("request.documents", f'duplicate document ID "{document.id}"')
        documents[document.id] = document

    anchors: dict[str, SourceAnchor] = {}
    for anchor in request.anchors:
        if not anchor.id.strip():
            raise _errors_at("request.anchors", "empty anchor ID")
        if anchor.id in anchors:
            raise _errors_at("request.anchors", f'duplicate anchor ID "{anchor.id}"')
        document = documents.get(anchor.source_id)
        if document is None or document.kind != anchor.kind:
            raise _errors_at("request.anchors", f'anchor "{anchor.id}" does not belong to a canonical document')
        if not anchor.locator.strip() or not anchor.text.strip():
            raise _errors_at("request.anchors", f'anchor "{anchor.id}" is missing locator or text')
        anchors[anchor.id] = anchor

    seen_fact_ids: dict[str, str] = {}

    def check(path: str, fact: Fact, expected_kind: SourceKind) -> None:
        _validate_fact(path, fact, expected_kind, anchors, seen_fact_ids)

    if result.job.title is not None:
        check("job.title", result.job.title, SourceKind.JD)
    for path, facts in (
        ("job.senioritySignals", result.job.seniority_signals),
        ("job.mustHave", result.job.must_have),
        ("job.niceToHave", result.job.nice_to_have),
        ("job.responsibilities", result.job.responsibilities),
        ("job.technicalTopics", result.job.technical_topics),
        ("job.businessConstraints", result.job.business_constraints),
    ):
        for index, fact in enumerate(facts):
            check(f"{path}[{index}]", fact, SourceKind.JD)

    if result.candidate.headline is not None:
        check("candidate.headline", result.candidate.headline, SourceKind.RESUME)
    for path, facts in (
        ("candidate.skills", result.candidate.skills),
        ("candidate.responsibilities", result.candidate.responsibilities),
        ("candidate.metrics", result.candidate.metrics),
    ):
        for index, fact in enumerate(facts):
            check(f"{path}[{index}]", fact, SourceKind.RESUME)

    seen_projects: set[str] = set()
    for project_index, project in enumerate(result.candidate.projects):
        path = f"candidate.projects[{project_index}]"
        if not project.id.strip():
            raise _errors_at(path, "project ID is required")
        if project.id in seen_projects:
            raise _errors_at(path, f'duplicate project ID "{project.id}"')
        seen_projects.add(project.id)
        check(f"{path}.name", project.name, SourceKind.RESUME)
        for name, facts in (
            ("responsibilities", project.responsibilities),
            ("metrics", project.metrics),
            ("technologies", project.technologies),
            ("highlights", project.highlights),
        ):
            for index, fact in enumerate(facts):
                check(f"{path}.{name}[{index}]", fact, SourceKind.RESUME)


def _validate_fact(
    path: str,
    fact: Fact,
    expected_kind: SourceKind,
    anchors: dict[str, SourceAnchor],
    seen_ids: dict[str, str],
) -> None:
    fact_id = fact.id.strip()
    value = fact.value.strip()
    if not fact_id:
        raise _errors_at(path, "fact ID is required")
    previous = seen_ids.get(fact_id)
    if previous is not None:
        raise _errors_at(path, f'fact ID "{fact_id}" already used at {previous}')
    seen_ids[fact_id] = path
    if not value:
        raise _errors_at(path, "fact value is required")
    if not fact.evidence_refs:
        raise _errors_at(path, "at least one evidence reference is required")

    supported = False
    seen_refs: set[str] = set()
    for ref in fact.evidence_refs:
        if ref.anchor_id in seen_refs:
            raise _errors_at(path, f'duplicate evidence anchor "{ref.anchor_id}"')
        seen_refs.add(ref.anchor_id)
        anchor = anchors.get(ref.anchor_id)
        if anchor is None:
            raise _errors_at(path, f'unknown evidence anchor "{ref.anchor_id}"')
        if (
            ref.source_id != anchor.source_id
            or ref.kind != anchor.kind
            or ref.locator != anchor.locator
            or ref.quote != anchor.text
        ):
            raise _errors_at(path, f'evidence metadata does not match anchor "{ref.anchor_id}"')
        if anchor.kind != expected_kind:
            raise _errors_at(
                path, f'evidence anchor "{ref.anchor_id}" has kind "{anchor.kind}", want "{expected_kind}"'
            )
        if normalize_grounding(value) in normalize_grounding(anchor.text):
            supported = True
    if not supported:
        raise _errors_at(path, f'value "{value}" is not extractive from its evidence')


def validate_fact(
    path: str,
    fact: Fact,
    expected_kind: SourceKind,
    anchors: dict[str, SourceAnchor],
    seen_ids: dict[str, str],
) -> None:
    _validate_fact(path, fact, expected_kind, anchors, seen_ids)
