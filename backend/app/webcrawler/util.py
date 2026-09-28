"""Shared string/list helpers mirroring webcrawler package-level funcs."""
from __future__ import annotations


def clean_list(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def localized_name(preferred: str, fallback: str) -> str:
    if (value := preferred.strip()):
        return value
    return fallback.strip()


def compact_runes(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"


def append_unique(values: list[str], value: str, limit: int) -> list[str]:
    if len(values) >= limit or value in values:
        return values
    return values + [value]


def merge_urls(first: list[str], second: list[str], limit: int) -> list[str]:
    result: list[str] = []
    for value in first + second:
        result = append_unique(result, value, limit)
    return result


def limit_strings(values: list[str], limit: int) -> list[str]:
    if len(values) <= limit:
        return values
    return values[:limit]


def prioritized_scripts(values: list[str], limit: int) -> list[str]:
    if len(values) <= limit:
        return list(values)
    leading = min(2, limit)
    trailing = limit - leading
    return list(values[:leading]) + list(values[len(values) - trailing:])
