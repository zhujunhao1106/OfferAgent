"""Typed profile domain mirroring Go profile/types.go."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SourceKind(str, Enum):
    JD = "jd"
    RESUME = "resume"


@dataclass
class DocumentInput:
    text: str
    source_id: str = ""
    name: str = ""


@dataclass
class Input:
    jd: DocumentInput = field(default_factory=lambda: DocumentInput(text=""))
    resume: DocumentInput = field(default_factory=lambda: DocumentInput(text=""))


@dataclass
class SourceDocument:
    id: str
    kind: SourceKind
    name: str = ""


@dataclass
class SourceAnchor:
    id: str
    source_id: str
    kind: SourceKind
    locator: str
    text: str


@dataclass
class EvidenceRef:
    source_id: str
    kind: SourceKind
    anchor_id: str
    locator: str
    quote: str


@dataclass
class Fact:
    id: str
    value: str
    evidence_refs: list[EvidenceRef] = field(default_factory=list)


@dataclass
class JobProfile:
    title: Fact | None = None
    seniority_signals: list[Fact] = field(default_factory=list)
    must_have: list[Fact] = field(default_factory=list)
    nice_to_have: list[Fact] = field(default_factory=list)
    responsibilities: list[Fact] = field(default_factory=list)
    technical_topics: list[Fact] = field(default_factory=list)
    business_constraints: list[Fact] = field(default_factory=list)


@dataclass
class ProjectProfile:
    id: str
    name: Fact
    responsibilities: list[Fact] = field(default_factory=list)
    metrics: list[Fact] = field(default_factory=list)
    technologies: list[Fact] = field(default_factory=list)
    highlights: list[Fact] = field(default_factory=list)


@dataclass
class CandidateProfile:
    headline: Fact | None = None
    skills: list[Fact] = field(default_factory=list)
    projects: list[ProjectProfile] = field(default_factory=list)
    responsibilities: list[Fact] = field(default_factory=list)
    metrics: list[Fact] = field(default_factory=list)


@dataclass
class Profile:
    job: JobProfile = field(default_factory=JobProfile)
    candidate: CandidateProfile = field(default_factory=CandidateProfile)


@dataclass
class AgentRequest:
    documents: list[SourceDocument] = field(default_factory=list)
    anchors: list[SourceAnchor] = field(default_factory=list)
