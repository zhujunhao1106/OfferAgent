"""Interview retriever adapter mirroring Go interview_retriever.go."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .index import Index

DEFAULT_INTERVIEW_LIMIT = 8
MAX_INTERVIEW_LIMIT = 20


class Focus(str, Enum):
    MIXED = "mixed"
    KNOWLEDGE = "knowledge"
    PROJECTS = "projects"


@dataclass
class KnowledgeQuery:
    model: str = ""
    focus: Focus = Focus.MIXED
    jd: str = ""
    resume: str = ""
    coverage_point_id: str = ""
    objective: str = ""
    question: str = ""
    previous_gaps: list[str] = field(default_factory=list)


@dataclass
class KnowledgeDocument:
    id: str
    title: str
    content: str


class InterviewRetriever:
    def __init__(self, index: Index, limit: int):
        if limit <= 0:
            limit = DEFAULT_INTERVIEW_LIMIT
        elif limit > MAX_INTERVIEW_LIMIT:
            limit = MAX_INTERVIEW_LIMIT
        self._index = index
        self._limit = limit

    def retrieve(self, query: KnowledgeQuery) -> list[KnowledgeDocument]:
        search_text = interview_search_text(query)
        entries = self._index.search(search_text, self._limit)
        return [
            KnowledgeDocument(
                id=entry.id,
                title=entry.question,
                content=(
                    f"知识主题：{entry.title}\n"
                    f"问题：{entry.question}\n"
                    f"参考内容：{entry.excerpt}\n"
                    f"来源：{entry.source}"
                ),
            )
            for entry in entries
        ]


def interview_search_text(query: KnowledgeQuery) -> str:
    parts: list[str] = []
    if value := bounded_material(query.objective, 1000):
        parts.append(value)
    if value := bounded_material(query.question, 1000):
        parts.append(value)
    if value := bounded_material("\n".join(query.previous_gaps), 2000):
        parts.append(value)
    if not query.objective.strip():
        if value := bounded_material(query.jd, 12000):
            parts.append(value)
        if value := bounded_material(query.resume, 12000):
            parts.append(value)
    if not parts:
        if query.focus == Focus.PROJECTS:
            parts.append("简历 项目 深挖 个人职责 量化指标 技术取舍")
        elif query.focus == Focus.KNOWLEDGE:
            parts.append("Agent 工程 原理 架构 工具调用 RAG 评测")
        else:
            parts.append("Agent 工程 项目 深挖 系统设计 技术取舍")
    return "\n".join(parts)


def bounded_material(value: str, max_runes: int) -> str:
    value = value.strip()
    if len(value) <= max_runes:
        return value
    return value[:max_runes]
