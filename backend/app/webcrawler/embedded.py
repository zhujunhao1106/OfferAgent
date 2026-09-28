"""Embedded JSON-LD/JSON job posting extraction mirroring Go webcrawler/embedded.go."""
from __future__ import annotations

import json

from .htmltree import (
    attribute,
    normalize_extracted_text,
    parse_html,
    script_text,
    walk_scripts,
)
from .types import Result
from .util import clean_list


def extract_embedded_job_posting(page: bytes, source: str) -> Result | None:
    try:
        document = parse_html(page.decode("utf-8", "replace"))
    except Exception:
        return None
    candidates: list[dict] = []

    def visit(node) -> None:
        type_name = attribute(node, "type").lower()
        if type_name not in ("application/ld+json", "application/json", "text/json"):
            return
        content = script_text(node).strip()
        if not content:
            return
        try:
            decoded = json.loads(content)
        except (ValueError, TypeError):
            return
        collect_embedded_jobs(decoded, source, type_name == "application/ld+json", candidates)

    walk_scripts(document, visit)
    best = max(candidates, key=lambda job: job["score"], default=None)
    if best is None:
        return None
    if best["score"] < 5 or not best["title"].strip() or len(best["description"] + best["requirements"]) < 80:
        return None

    sections = ["职位：" + best["title"].strip()]
    metadata = clean_list([best["employment"], best["organization"]])
    if metadata:
        sections.append("招聘类型：" + " · ".join(metadata))
    locations = clean_list(best["locations"])
    if locations:
        sections.append("工作地点：" + "、".join(locations))
    if best["identifier"]:
        sections.append("职位编号：" + best["identifier"])
    if best["description"]:
        sections.append("岗位职责\n" + best["description"])
    if best["requirements"]:
        sections.append("岗位要求与加分项\n" + best["requirements"])
    return Result(
        text="\n\n".join(sections), title=best["title"].strip(),
        source=source, provider="embedded-json",
    )


def collect_embedded_jobs(value, source: str, schema: bool, result: list[dict]) -> None:
    if isinstance(value, list):
        for item in value:
            collect_embedded_jobs(item, source, schema, result)
    elif isinstance(value, dict):
        if "@graph" in value:
            collect_embedded_jobs(value["@graph"], source, True, result)
        candidate = embedded_job_from_map(value, source, schema)
        if candidate is not None:
            result.append(candidate)
        for key, child in value.items():
            if key == "@graph":
                continue
            if isinstance(child, (dict, list)):
                collect_embedded_jobs(child, source, schema, result)


def embedded_job_from_map(value: dict, source: str, schema: bool) -> dict | None:
    is_job_posting = schema and json_type_contains(value.get("@type"), "JobPosting")
    title = first_string(value, "title", "name", "jobTitle", "positionName")
    description = first_string(value, "responsibilities", "description", "jobDescription")
    requirements = first_string(value, "qualifications", "requirements", "requirement", "experienceRequirements")
    if not is_job_posting and (not description or not requirements):
        return None

    job = {
        "title": text_from_html(title),
        "description": text_from_html(description),
        "requirements": text_from_html(requirements),
        "employment": string_value(value.get("employmentType")),
        "identifier": embedded_identifier(value.get("identifier")),
        "organization": nested_string(value.get("hiringOrganization"), "name"),
        "locations": embedded_locations(value.get("jobLocation")),
        "score": 0,
    }
    if is_job_posting:
        job["score"] += 5
    if job["title"]:
        job["score"] += 2
    if len(job["description"]) >= 50:
        job["score"] += 2
    if len(job["requirements"]) >= 30:
        job["score"] += 2
    if job["identifier"] and job["identifier"] in source:
        job["score"] += 3
    return job


def json_type_contains(value, expected: str) -> bool:
    if isinstance(value, str):
        return value.lower() == expected.lower()
    if isinstance(value, list):
        return any(json_type_contains(item, expected) for item in value)
    return False


def first_string(value: dict, *keys: str) -> str:
    for key in keys:
        if (result := string_value(value.get(key))):
            return result
    return ""


def string_value(value) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def nested_string(value, key: str) -> str:
    if isinstance(value, dict):
        return string_value(value.get(key))
    return ""


def embedded_identifier(value) -> str:
    if (text := string_value(value)):
        return text
    if isinstance(value, dict):
        return first_string(value, "value", "name")
    return ""


def embedded_locations(value) -> list[str]:
    items = value if isinstance(value, list) else [value]
    locations: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        address = item.get("address") if isinstance(item.get("address"), dict) else {}
        parts = clean_list([first_string(address, "addressLocality", "addressRegion"), string_value(address.get("addressCountry"))])
        if parts:
            locations.append(" ".join(parts))
    return locations


def text_from_html(value: str) -> str:
    value = value.strip()
    if not value or "<" not in value:
        return normalize_extracted_text(value)
    try:
        document = parse_html("<body>" + value + "</body>")
    except Exception:
        return normalize_extracted_text(value)
    title = [""]
    parts: list[str] = []
    from .htmltree import _walk_html
    _walk_html(document, False, title, parts)
    return normalize_extracted_text("".join(parts))
