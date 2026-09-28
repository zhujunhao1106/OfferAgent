"""Markdown parsing mirroring Go markdown.go."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .index import Entry

DEFAULT_EXCERPT_RUNES = 520

QUESTION_HEADING = re.compile(r"(?m)^#{2,6}[ \t]+Q[ \t]*[：:][ \t]*(.+?)[ \t]*\r?$")
TITLE_HEADING = re.compile(r"(?m)^#[ \t]+([^#\r\n].*?)[ \t]*\r?$")
FRONT_TITLE = re.compile(r"(?m)^title:[ \t]*[\"']?(.+?)[\"']?[ \t]*\r?$")
MARKDOWN_LINK = re.compile(r"!?\[([^]]*)\]\([^)]*\)")
HTML_TAG = re.compile(r"<[^>]+>")
SPACE_RUN = re.compile(r"\s+")


def parse_markdown(source: str, markdown: str) -> list[Entry]:
    matches = list(QUESTION_HEADING.finditer(markdown))
    if not matches:
        return []
    title = markdown_title(source, markdown)
    entries: list[Entry] = []
    ordinals: dict[str, int] = {}
    for idx, match in enumerate(matches):
        question = markdown[match.start(1):match.end(1)].strip()
        if not question:
            continue
        body_start = match.end(0)
        body_end = len(markdown)
        if idx + 1 < len(matches):
            body_end = matches[idx + 1].start(0)
        body = markdown[body_start:body_end].strip()
        plain = plain_text(body)
        ordinal_key = question.lower()
        ordinal = ordinals.get(ordinal_key, 0)
        ordinals[ordinal_key] = ordinal + 1
        entries.append(Entry(
            id=entry_id(source, question, ordinal),
            source=source,
            title=title,
            question=question,
            excerpt=truncate_runes(plain, DEFAULT_EXCERPT_RUNES),
            body=plain,
        ))
    return entries


def markdown_title(source: str, markdown: str) -> str:
    match = TITLE_HEADING.search(markdown)
    if match:
        return match.group(1).strip()
    match = FRONT_TITLE.search(markdown)
    if match:
        return match.group(1).strip()
    base = Path(source).name
    return Path(base).stem


def entry_id(source: str, question: str, ordinal: int) -> str:
    digest = hashlib.sha256(f"{source}\x00{question}\x00{ordinal}".encode()).hexdigest()
    return "kb_" + digest[:20]


def plain_text(markdown: str) -> str:
    lines = markdown.replace("\r\n", "\n").split("\n")
    cleaned: list[str] = []
    in_fence = False
    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("```") or trimmed.startswith("~~~"):
            in_fence = not in_fence
            continue
        if not in_fence:
            trimmed = trimmed.lstrip("#> -*+\t")
        trimmed = MARKDOWN_LINK.sub(r"\1", trimmed)
        trimmed = HTML_TAG.sub(" ", trimmed)
        trimmed = trimmed.replace("**", "").replace("__", "").replace("`", "").replace("~~", "")
        if trimmed:
            cleaned.append(trimmed)
    return SPACE_RUN.sub(" ", " ".join(cleaned)).strip()


def truncate_runes(value: str, limit: int) -> str:
    if limit <= 0 or len(value) <= limit:
        return value
    return value[:limit].strip() + "..."
