"""Knowledge base: Markdown parsing + BM25 retrieval (mirrors Go internal/knowledge)."""
from .index import Entry, Index, build_index, load, new_index
from .markdown import entry_id, markdown_title, parse_markdown, plain_text, truncate_runes
from .retriever import (
    Focus,
    InterviewRetriever,
    KnowledgeDocument,
    KnowledgeQuery,
    bounded_material,
    interview_search_text,
)
from .tokenize import is_han, normalize_for_phrase, tokenize, unique

__all__ = [
    "Entry", "Index", "build_index", "load", "new_index",
    "entry_id", "markdown_title", "parse_markdown", "plain_text", "truncate_runes",
    "Focus", "InterviewRetriever", "KnowledgeDocument", "KnowledgeQuery",
    "bounded_material", "interview_search_text",
    "is_han", "normalize_for_phrase", "tokenize", "unique",
]
