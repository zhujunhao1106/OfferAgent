"""Deterministic, grounded JD/resume extraction mirroring Go profile/extractor.go."""
from __future__ import annotations

import re
from enum import IntEnum

from .types import (
    AgentRequest,
    CandidateProfile,
    EvidenceRef,
    Fact,
    Input,
    JobProfile,
    Profile,
    ProjectProfile,
    SourceAnchor,
    SourceDocument,
    SourceKind,
)
from .validate import validate

YEARS_PATTERN = re.compile(
    r"(?i)(?:\d+\s*(?:年|years?))|(?:\bsenior\b|\bstaff\b|\bprincipal\b|\blead\b|高级|资深|专家|负责人)"
)
METRIC_PATTERN = re.compile(
    r"(?i)(?:\d+(?:\.\d+)?\s*(?:%|％|倍|万|亿|ms|s|秒|分钟|小时|qps|tps|rps|k|m|gb|tb|人|用户|请求|节点|台))"
    r"|(?:p(?:50|90|95|99)|qps|tps|rps)\s*[:=]?\s*\d+"
)

TECHNOLOGY_NAMES = [
    "Go", "Golang", "Java", "Python", "JavaScript", "TypeScript", "Node.js", "React", "Vue", "Next.js",
    "MySQL", "PostgreSQL", "SQLite", "Redis", "Kafka", "Pulsar", "RabbitMQ", "Elasticsearch", "ClickHouse",
    "Docker", "Kubernetes", "AWS", "Azure", "GCP", "gRPC", "GraphQL", "REST", "OpenTelemetry",
    "LLM", "RAG", "Agent", "LangChain", "LangGraph", "PyTorch", "TensorFlow",
]

SENTENCE_BREAKS = "。！？!?；;"
BULLET_PREFIXES = ["- ", "* ", "+ ", "• ", "· ", "> "]


class Section(IntEnum):
    UNKNOWN = 0
    REQUIREMENTS = 1
    NICE_TO_HAVE = 2
    RESPONSIBILITIES = 3
    PROJECTS = 4
    SKILLS = 5
    EXPERIENCE = 6


class DeterministicExtractor:
    def extract(self, input: Input) -> Profile:
        request = build_agent_request(input)
        result = Profile(
            job=JobProfile(),
            candidate=CandidateProfile(),
        )
        result.job = _extract_job(_anchors_of_kind(request.anchors, SourceKind.JD), result.job)
        result.candidate = _extract_candidate(_anchors_of_kind(request.anchors, SourceKind.RESUME), result.candidate)
        validate(request, result)
        return result


def build_agent_request(input: Input) -> AgentRequest:
    request = AgentRequest()
    seen_sources: set[str] = set()
    for kind, fallback_id, document in (
        (SourceKind.JD, "jd", input.jd),
        (SourceKind.RESUME, "resume", input.resume),
    ):
        text = document.text.strip()
        if not text:
            continue
        source_id = document.source_id.strip()
        if not source_id:
            source_id = fallback_id
        if source_id in seen_sources:
            raise ValueError(f'profile: duplicate source ID "{source_id}"')
        seen_sources.add(source_id)
        request.documents.append(SourceDocument(id=source_id, kind=kind, name=document.name.strip()))
        for index, segment in enumerate(_split_material(document.text)):
            anchor_id = f"{source_id}:{index + 1:03d}"
            request.anchors.append(
                SourceAnchor(
                    id=anchor_id, source_id=source_id, kind=kind,
                    locator=segment.locator, text=segment.text,
                )
            )
    if not request.documents:
        raise NoMaterialsError("profile: JD or resume text is required")
    return request


class NoMaterialsError(ValueError):
    pass


class MaterialSegment:
    def __init__(self, text: str, locator: str):
        self.text = text
        self.locator = locator


def _split_material(value: str) -> list[MaterialSegment]:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    segments: list[MaterialSegment] = []
    for line_index, line in enumerate(normalized.split("\n")):
        parts = _split_sentence_parts(line)
        for part_index, part in enumerate(parts):
            part = part.strip()
            if _rune_count(_clean_value(part)) < 2:
                continue
            locator = f"line:{line_index + 1}"
            if len(parts) > 1:
                locator = f"line:{line_index + 1},part:{part_index + 1}"
            segments.append(MaterialSegment(text=part, locator=locator))
    return segments


def _split_sentence_parts(line: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for index, ch in enumerate(line):
        if ch not in SENTENCE_BREAKS:
            continue
        end = index + 1
        parts.append(line[start:end])
        start = end
    if start < len(line):
        parts.append(line[start:])
    if not parts:
        return [line]
    return parts


def _extract_job(anchors: list[SourceAnchor], result: JobProfile) -> JobProfile:
    current_section = Section.UNKNOWN
    seen: dict[str, set[str]] = {
        "seniority": set(), "must": set(), "nice": set(),
        "responsibility": set(), "topic": set(), "constraint": set(),
    }
    for anchor in anchors:
        value = _clean_value(anchor.text)
        if (detected := _heading_section(value)) is not None:
            current_section = detected
            continue
        inline = _inline_section(value)
        if inline is not None:
            current_section, body = inline
            value = body
        if not value:
            continue
        if result.title is None:
            if (title := _prefixed_value(value, "职位", "岗位", "职位名称", "position", "role"))[1]:
                result.title = _fact_from_anchor("job-title", title[0], anchor)
                continue
            if current_section == Section.UNKNOWN and _rune_count(value) <= 80 and not _looks_like_bullet(anchor.text):
                result.title = _fact_from_anchor("job-title", value, anchor)
                continue

        lower = value.lower()
        if current_section == Section.NICE_TO_HAVE:
            result.nice_to_have = _append_fact(result.nice_to_have, "job-nice", value, anchor, seen["nice"])
        elif current_section == Section.RESPONSIBILITIES:
            result.responsibilities = _append_fact(result.responsibilities, "job-responsibility", value, anchor, seen["responsibility"])
        elif current_section == Section.REQUIREMENTS:
            result.must_have = _append_fact(result.must_have, "job-must", value, anchor, seen["must"])
        elif _contains_any(lower, "加分", "优先", "preferred", "nice to have", "bonus", "a plus"):
            result.nice_to_have = _append_fact(result.nice_to_have, "job-nice", value, anchor, seen["nice"])
        elif _contains_any(lower, "必须", "要求", "任职", "具备", "熟悉", "精通", "掌握", "至少", "年以上", "must", "required", "requirement", "proficient", "experience with", "years of"):
            result.must_have = _append_fact(result.must_have, "job-must", value, anchor, seen["must"])
        elif _contains_any(lower, "负责", "职责", "主导", "设计", "建设", "维护", "交付", "develop", "build", "design", "maintain", "deliver", "own"):
            result.responsibilities = _append_fact(result.responsibilities, "job-responsibility", value, anchor, seen["responsibility"])

        if YEARS_PATTERN.search(value):
            result.seniority_signals = _append_fact(result.seniority_signals, "job-seniority", value, anchor, seen["seniority"])
        if _contains_any(lower, "高并发", "可用性", "低延迟", "安全", "合规", "成本", "scalab", "availability", "latency", "security", "compliance", "cost"):
            result.business_constraints = _append_fact(result.business_constraints, "job-constraint", value, anchor, seen["constraint"])
        for technology in _technologies_in(value):
            result.technical_topics = _append_fact(result.technical_topics, "job-topic", technology, anchor, seen["topic"])
    return result


def _extract_candidate(anchors: list[SourceAnchor], result: CandidateProfile) -> CandidateProfile:
    current_section = Section.UNKNOWN
    current_project = -1
    seen_skills: set[str] = set()
    seen_responsibilities: set[str] = set()
    seen_metrics: set[str] = set()
    for anchor in anchors:
        value = _clean_value(anchor.text)
        declared_project = False
        if (detected := _heading_section(value)) is not None:
            current_section = detected
            if detected != Section.PROJECTS:
                current_project = -1
            continue
        inline = _inline_section(value)
        if inline is not None:
            current_section, body = inline
            if current_section != Section.PROJECTS:
                current_project = -1
            value = body
        if not value:
            continue

        project = _project_name(value, anchor.text, current_section)
        if project is not None:
            name, body = project
            project_id = f"candidate-project-{len(result.projects) + 1:03d}"
            result.projects.append(
                ProjectProfile(id=project_id, name=_fact_from_anchor(f"{project_id}-name", name, anchor))
            )
            current_project = len(result.projects) - 1
            declared_project = True
            value = body
            if not value:
                continue

        if not declared_project and result.headline is None and current_section == Section.UNKNOWN and not _looks_like_bullet(anchor.text):
            result.headline = _fact_from_anchor("candidate-headline", value, anchor)
            continue

        lower = value.lower()
        is_responsibility = _contains_any(
            lower, "负责", "主导", "独立", "设计", "实现", "搭建", "优化", "交付",
            "led", "owned", "designed", "implemented", "built", "delivered", "maintained",
        )
        is_metric = bool(METRIC_PATTERN.search(value)) and _contains_any(
            lower, "提升", "降低", "减少", "增长", "缩短", "节省", "达到", "支持", "覆盖", "从", "至",
            "to ", "increased", "reduced", "decreased", "improved", "saved", "served", "handled",
        )

        if is_responsibility:
            result.responsibilities = _append_fact(result.responsibilities, "candidate-responsibility", value, anchor, seen_responsibilities)
            if current_project >= 0:
                project = result.projects[current_project]
                project.responsibilities = _append_fact(project.responsibilities, f"{project.id}-responsibility", value, anchor, None)
        if is_metric:
            result.metrics = _append_fact(result.metrics, "candidate-metric", value, anchor, seen_metrics)
            if current_project >= 0:
                project = result.projects[current_project]
                project.metrics = _append_fact(project.metrics, f"{project.id}-metric", value, anchor, None)

        technologies = _technologies_in(value)
        if current_section == Section.SKILLS:
            technologies = technologies + _skill_list_values(value)
        for technology in _unique_strings(technologies):
            result.skills = _append_fact(result.skills, "candidate-skill", technology, anchor, seen_skills)
            if current_project >= 0:
                project = result.projects[current_project]
                project.technologies = _append_fact(project.technologies, f"{project.id}-technology", technology, anchor, None)
        if current_project >= 0 and not is_responsibility and not is_metric and current_section == Section.PROJECTS:
            project = result.projects[current_project]
            project.highlights = _append_fact(project.highlights, f"{project.id}-highlight", value, anchor, None)
    return result


def _anchors_of_kind(anchors: list[SourceAnchor], kind: SourceKind) -> list[SourceAnchor]:
    return [anchor for anchor in anchors if anchor.kind == kind]


def _fact_from_anchor(id: str, value: str, anchor: SourceAnchor) -> Fact:
    return Fact(
        id=id,
        value=value.strip(),
        evidence_refs=[EvidenceRef(
            source_id=anchor.source_id, kind=anchor.kind, anchor_id=anchor.id,
            locator=anchor.locator, quote=anchor.text,
        )],
    )


def _append_fact(facts: list[Fact], prefix: str, value: str, anchor: SourceAnchor, seen: set[str] | None) -> list[Fact]:
    value = value.strip()
    key = value.lower()
    if not value:
        return facts
    if seen is not None:
        if key in seen:
            return facts
        seen.add(key)
    id = f"{prefix}-{len(facts) + 1:03d}"
    return facts + [_fact_from_anchor(id, value, anchor)]


_HEADING_SECTIONS = {
    "任职要求": Section.REQUIREMENTS, "岗位要求": Section.REQUIREMENTS, "职位要求": Section.REQUIREMENTS,
    "任职资格": Section.REQUIREMENTS, "requirements": Section.REQUIREMENTS, "qualifications": Section.REQUIREMENTS,
    "must have": Section.REQUIREMENTS, "must-have": Section.REQUIREMENTS,
    "加分项": Section.NICE_TO_HAVE, "优先条件": Section.NICE_TO_HAVE, "优先项": Section.NICE_TO_HAVE,
    "nice to have": Section.NICE_TO_HAVE, "nice-to-have": Section.NICE_TO_HAVE, "preferred": Section.NICE_TO_HAVE,
    "bonus": Section.NICE_TO_HAVE,
    "岗位职责": Section.RESPONSIBILITIES, "工作职责": Section.RESPONSIBILITIES, "职位职责": Section.RESPONSIBILITIES,
    "职责": Section.RESPONSIBILITIES, "responsibilities": Section.RESPONSIBILITIES, "what you will do": Section.RESPONSIBILITIES,
    "项目经历": Section.PROJECTS, "项目经验": Section.PROJECTS, "项目": Section.PROJECTS,
    "projects": Section.PROJECTS, "project experience": Section.PROJECTS,
    "技能": Section.SKILLS, "专业技能": Section.SKILLS, "技术栈": Section.SKILLS,
    "skills": Section.SKILLS, "technologies": Section.SKILLS, "tech stack": Section.SKILLS,
    "工作经历": Section.EXPERIENCE, "工作经验": Section.EXPERIENCE, "experience": Section.EXPERIENCE,
    "employment": Section.EXPERIENCE, "work experience": Section.EXPERIENCE,
}


def _heading_section(value: str) -> Section | None:
    heading = value.strip().lower()
    heading = heading.strip("#*：: -_[]【】")
    return _HEADING_SECTIONS.get(heading)


_INLINE_SECTION_GROUPS = [
    (Section.REQUIREMENTS, ["任职要求", "岗位要求", "职位要求", "任职资格", "requirements", "qualifications", "must have", "must-have"]),
    (Section.NICE_TO_HAVE, ["加分项", "优先条件", "优先项", "nice to have", "nice-to-have", "preferred", "bonus"]),
    (Section.RESPONSIBILITIES, ["岗位职责", "工作职责", "职位职责", "职责", "responsibilities", "what you will do"]),
    (Section.PROJECTS, ["项目经历", "项目经验", "projects", "project experience"]),
    (Section.SKILLS, ["技能", "专业技能", "技术栈", "skills", "technologies", "tech stack"]),
    (Section.EXPERIENCE, ["工作经历", "工作经验", "experience", "employment", "work experience"]),
]


def _inline_section(value: str) -> tuple[Section, str] | None:
    for section, prefixes in _INLINE_SECTION_GROUPS:
        result = _prefixed_value(value, *prefixes)
        if result[1]:
            return section, result[0]
    return None


def _project_name(value: str, raw: str, current_section: Section) -> tuple[str, str] | None:
    result = _prefixed_value(value, "项目名称", "项目", "project")
    if result[1]:
        # Go checks ok against the pre-trim name, so keep the tuple even if empty.
        return _trim_project_date(result[0]), ""
    trimmed = value.strip()
    lower = trimmed.lower()
    for prefix in ("项目 ", "project "):
        if not lower.startswith(prefix):
            continue
        remainder = trimmed[len(prefix):].strip()
        name, body = remainder, ""
        if (separator := remainder.find("：")) >= 0:
            name = remainder[:separator].strip()
            body = remainder[separator + 1:].strip()
        elif (separator := remainder.find(":")) >= 0:
            name = remainder[:separator].strip()
            body = remainder[separator + 1:].strip()
        name = _trim_project_date(name)
        if not name:
            return None
        return name, body
    if current_section != Section.PROJECTS or _looks_like_bullet(raw):
        return None
    if _rune_count(value) > 100 or _contains_any(value.lower(), "负责", "主导", "实现", "优化", "built", "designed", "implemented"):
        return None
    if raw.strip().startswith("#") or "|" in value or "｜" in value:
        return _trim_project_date(value), ""
    return value, ""


def _trim_project_date(value: str) -> str:
    for separator in ("|", "｜", "\t"):
        if (index := value.find(separator)) > 0:
            value = value[:index]
    return value.strip()


def _prefixed_value(value: str, *prefixes: str) -> tuple[str, bool]:
    lower = value.lower()
    for prefix in prefixes:
        prefix = prefix.lower()
        for delimiter in ("：", ":"):
            marker = prefix + delimiter
            if lower.startswith(marker):
                result = value[len(marker):].strip()
                return result, result != ""
    return "", False


def _clean_value(value: str) -> str:
    value = value.strip()
    while value.startswith("#"):
        value = value[1:].strip()
    for prefix in BULLET_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix):].strip()
    for index, ch in enumerate(value):
        if not ch.isdigit():
            if index > 0 and ch in (".", "、", ")", "）"):
                return value[index + 1:].strip()
            break
    return value


def _looks_like_bullet(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    if value[0] in "-*+•·>":
        return True
    for ch in value:
        if ch.isdigit():
            continue
        return ch in (".", "、", ")", "）")
    return False


def _technologies_in(value: str) -> list[str]:
    result: list[str] = []
    for technology in TECHNOLOGY_NAMES:
        if _contains_technology(value, technology):
            result.append(technology)
    return _unique_strings(result)


def _contains_technology(value: str, technology: str) -> bool:
    value = value.lower()
    technology = technology.lower()
    search_from = 0
    while search_from < len(value):
        position = value.find(technology, search_from)
        if position < 0:
            return False
        start = position
        end = start + len(technology)
        before_word = _is_ascii_token_rune(value[start - 1]) if start > 0 else False
        after_word = _is_ascii_token_rune(value[end]) if end < len(value) else False
        if not before_word and not after_word:
            return True
        search_from = start + 1
    return False


def _is_ascii_token_rune(ch: str) -> bool:
    return ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_"


_SKILL_SPLIT_RE = re.compile(r"[,，、/|｜;；]")


def _skill_list_values(value: str) -> list[str]:
    result: list[str] = []
    for part in _SKILL_SPLIT_RE.split(value):
        part = part.strip()
        prefixed = _prefixed_value(part, "技能", "技术栈", "skills", "technologies")
        if prefixed[1]:
            part = prefixed[0]
        count = _rune_count(part)
        if 2 <= count <= 40:
            result.append(part)
    return result


def _unique_strings(values: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        value = value.strip()
        if not value:
            continue
        key = value.lower()
        existing = seen.get(key)
        if existing is None or len(value) < len(existing):
            seen[key] = value
    return [seen[key] for key in sorted(seen)]


def _contains_any(value: str, *candidates: str) -> bool:
    for candidate in candidates:
        if candidate in value:
            return True
    return False


def _rune_count(value: str) -> int:
    return len(value)
