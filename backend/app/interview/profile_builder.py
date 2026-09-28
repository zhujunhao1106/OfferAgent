"""Typed profile adapter mirroring Go interview/typed_profile_adapter.go."""
from __future__ import annotations

from ..profile import DeterministicExtractor, build_agent_request
from ..profile.types import AgentRequest as TypedAgentRequest
from ..profile.types import DocumentInput as TypedDocumentInput
from ..profile.types import Input as TypedInput
from ..profile.types import Profile as TypedProfile
from ..profile.types import SourceKind as TypedSourceKind
from .sources import (
    concise,
    evidence_from_anchor,
    interleave_coverage,
    merge_knowledge_documents,
    public_anchor_label,
    validate_evidence_refs,
)
from .types import (
    CoveragePoint,
    EvidenceRef,
    Focus,
    JDProfile,
    MaterialsInput,
    Profile,
    ProfilePoint,
    ResumeProfile,
    SourceAnchor,
    SourceDocument,
    SourceIndex,
    SourceKind,
)


def _typed_interview_source_kind(kind: TypedSourceKind) -> SourceKind:
    if kind == TypedSourceKind.JD:
        return SourceKind.JD
    if kind == TypedSourceKind.RESUME:
        return SourceKind.RESUME
    raise ValueError(f'interview: unsupported typed profile source kind "{kind}"')


def _typed_profile_input(materials: MaterialsInput) -> TypedInput:
    result = TypedInput()
    if materials.jd is not None:
        result.jd = TypedDocumentInput(source_id="jd", name=materials.jd.name.strip(), text=materials.jd.text)
    if materials.resume is not None:
        result.resume = TypedDocumentInput(source_id="resume", name=materials.resume.name.strip(), text=materials.resume.text)
    return result


def _typed_interview_source_index(request, input: TypedInput) -> SourceIndex:
    index = SourceIndex()
    for document in request.documents:
        kind = _typed_interview_source_kind(document.kind)
        content = input.jd.text
        if document.kind == TypedSourceKind.RESUME:
            content = input.resume.text
        index.documents[document.id] = SourceDocument(
            id=document.id, kind=kind, name=document.name, content=content,
        )
    for anchor in request.anchors:
        kind = _typed_interview_source_kind(anchor.kind)
        if anchor.id in index.anchors:
            raise ValueError(f'interview: duplicate typed anchor "{anchor.id}"')
        index.anchors[anchor.id] = SourceAnchor(
            id=anchor.id, sourceId=anchor.source_id, kind=kind,
            locator=anchor.locator, text=anchor.text,
        )
        index.order.append(anchor.id)
    return index


def _typed_canonical_evidence(index: SourceIndex, refs: list) -> list[EvidenceRef]:
    result: list[EvidenceRef] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.anchor_id in seen:
            continue
        if ref.anchor_id not in index.anchors:
            raise ValueError(f'interview: typed profile references unknown anchor "{ref.anchor_id}"')
        seen.add(ref.anchor_id)
        result.append(evidence_from_anchor(index.anchors[ref.anchor_id]))
    if not result:
        raise ValueError("interview: typed profile point requires evidence")
    return result


def _typed_profile_point(prefix: str, position: int, fact, index: SourceIndex) -> ProfilePoint:
    refs = _typed_canonical_evidence(index, fact.evidence_refs)
    return ProfilePoint(id=f"{prefix}-{position:03d}", label=concise(fact.value, 120), evidenceRefs=refs)


def _typed_coverage_signature(label: str, refs: list[EvidenceRef]) -> str:
    parts = [" ".join(label.lower().split())]
    parts.extend(ref.anchorId for ref in refs)
    return "|".join(parts)


def _append_typed_coverage(points, prefix, area, fact, index, seen) -> list[CoveragePoint]:
    refs = _typed_canonical_evidence(index, fact.evidence_refs)
    label = concise(fact.value, 120)
    signature = _typed_coverage_signature(label, refs)
    if signature in seen:
        return points
    seen.add(signature)
    points.append(CoveragePoint(
        id=f"{prefix}-{len(points) + 1:03d}", area=area, label=label, evidenceRefs=refs,
    ))
    return points


def _adapt_typed_profile(extracted, index: SourceIndex):
    result = Profile(jd=JDProfile(), resume=ResumeProfile())
    if extracted.job.title is not None:
        result.jd.title = extracted.job.title.value
    if extracted.candidate.headline is not None:
        result.resume.headline = extracted.candidate.headline.value

    for fact in list(extracted.job.must_have) + list(extracted.job.nice_to_have):
        result.jd.requirements.append(
            _typed_profile_point("typed-jd-requirement", len(result.jd.requirements) + 1, fact, index)
        )
    for fact in extracted.job.responsibilities:
        result.jd.responsibilities.append(
            _typed_profile_point("typed-jd-responsibility", len(result.jd.responsibilities) + 1, fact, index)
        )

    seen_skills: set[str] = set()
    for fact in extracted.candidate.skills:
        value = fact.value.strip()
        key = value.lower()
        if not value or key in seen_skills:
            continue
        seen_skills.add(key)
        result.resume.skills.append(value)

    for position, project in enumerate(extracted.candidate.projects):
        refs = list(project.name.evidence_refs)
        for group in (project.responsibilities, project.metrics, project.technologies, project.highlights):
            for fact in group:
                refs.extend(fact.evidence_refs)
        canonical = _typed_canonical_evidence(index, refs)
        result.resume.projects.append(ProfilePoint(
            id=f"typed-resume-project-{position + 1:03d}", label=concise(project.name.value, 120), evidenceRefs=canonical,
        ))

    jd_coverage: list[CoveragePoint] = []
    project_coverage: list[CoveragePoint] = []
    seen_jd: set[str] = set()
    seen_project: set[str] = set()
    for group in (
        extracted.job.must_have,
        extracted.job.responsibilities,
        extracted.job.nice_to_have,
        extracted.job.seniority_signals,
        extracted.job.business_constraints,
        extracted.job.technical_topics,
    ):
        for fact in group:
            jd_coverage = _append_typed_coverage(jd_coverage, "typed-jd", Focus.KNOWLEDGE, fact, index, seen_jd)

    for position, project in enumerate(extracted.candidate.projects):
        refs = list(project.name.evidence_refs)
        for group in (project.responsibilities, project.metrics, project.technologies, project.highlights):
            for fact in group:
                refs.extend(fact.evidence_refs)
        canonical = _typed_canonical_evidence(index, refs)
        point = CoveragePoint(
            id=f"typed-project-{position + 1:03d}", area=Focus.PROJECTS,
            label=concise(project.name.value, 120), evidenceRefs=canonical,
        )
        signature = _typed_coverage_signature(point.label, point.evidenceRefs)
        if signature not in seen_project:
            seen_project.add(signature)
            project_coverage.append(point)

    for group in (extracted.candidate.responsibilities, extracted.candidate.metrics):
        for fact in group:
            project_coverage = _append_typed_coverage(
                project_coverage, "typed-resume", Focus.PROJECTS, fact, index, seen_project
            )
    for fact in extracted.candidate.skills:
        jd_coverage = _append_typed_coverage(jd_coverage, "typed-skill", Focus.KNOWLEDGE, fact, index, seen_jd)

    if not jd_coverage and extracted.job.title is not None:
        jd_coverage = _append_typed_coverage(jd_coverage, "typed-jd", Focus.KNOWLEDGE, extracted.job.title, index, seen_jd)
    if not project_coverage and extracted.candidate.headline is not None:
        project_coverage = _append_typed_coverage(
            project_coverage, "typed-resume", Focus.PROJECTS, extracted.candidate.headline, index, seen_project
        )
    return result, jd_coverage, project_coverage


def _validate_adapted_typed_profile(index: SourceIndex, result: Profile) -> None:
    for group in (result.jd.requirements, result.jd.responsibilities, result.resume.projects):
        for point in group:
            try:
                validate_evidence_refs(index, point.evidenceRefs, None, True)
            except ValueError as exc:
                raise ValueError(f'interview: validate adapted profile point "{point.id}": {exc}') from exc
    for point in result.coverage:
        try:
            validate_evidence_refs(index, point.evidenceRefs, None, True)
        except ValueError as exc:
            raise ValueError(f'interview: validate adapted coverage point "{point.id}": {exc}') from exc


def extract_typed_profile(materials: MaterialsInput, knowledge: list, focus: Focus) -> tuple[Profile, SourceIndex]:
    input = _typed_profile_input(materials)
    request = TypedAgentRequest()
    extracted = TypedProfile()
    if input.jd.text.strip() or input.resume.text.strip():
        request = build_agent_request(input)
        extracted = DeterministicExtractor().extract(input)

    index = _typed_interview_source_index(request, input)
    result, jd_coverage, project_coverage = _adapt_typed_profile(extracted, index)

    knowledge_refs = merge_knowledge_documents(index, knowledge, 0)
    knowledge_coverage: list[CoveragePoint] = []
    for position, ref in enumerate(knowledge_refs):
        anchor = index.anchors.get(ref.anchorId)
        if anchor is None:
            raise ValueError(f'interview: missing merged knowledge anchor "{ref.anchorId}"')
        knowledge_coverage.append(CoveragePoint(
            id=f"typed-knowledge-{position + 1:03d}", area=Focus.KNOWLEDGE,
            label=public_anchor_label(anchor), evidenceRefs=[evidence_from_anchor(anchor)],
        ))

    knowledge_oriented = list(jd_coverage) + knowledge_coverage
    if focus == Focus.PROJECTS:
        result.coverage = list(project_coverage) + knowledge_oriented
    elif focus == Focus.KNOWLEDGE:
        result.coverage = knowledge_oriented + list(project_coverage)
    else:
        result.coverage = interleave_coverage(project_coverage, knowledge_oriented)

    _validate_adapted_typed_profile(index, result)
    return result, index


class ProfileBuilder:
    def __init__(self):
        self._extractor = DeterministicExtractor()

    def build(self, materials: MaterialsInput, knowledge: list, focus: Focus) -> tuple[Profile, SourceIndex]:
        return extract_typed_profile(materials, knowledge, focus)
