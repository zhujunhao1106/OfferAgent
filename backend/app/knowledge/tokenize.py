"""Dependency-free tokenizer mirroring Go tokenize.go (CJK unigram + bigram)."""
from __future__ import annotations

# Go unicode.Han ranges (Go 1.26 tables). We cover the ideograph ranges; radicals
# (2E80-2EFF, 2F00-2FD5) and a few CJK symbols are included for exact parity even
# though they do not appear in the interview corpus.
_HAN_RANGES = [
    (0x2E80, 0x2E99), (0x2E9B, 0x2EF3), (0x2F00, 0x2FD5),
    (0x3005, 0x3005), (0x3007, 0x3007), (0x3021, 0x3029),
    (0x3038, 0x303B), (0x3400, 0x4DB5), (0x4E00, 0x9FFF),
    (0xF900, 0xFA6D), (0xFA70, 0xFAD9),
]
_HAN_RANGES_32 = [
    (0x20000, 0x2A6D6), (0x2A700, 0x2B734), (0x2B740, 0x2B81D),
    (0x2B820, 0x2CEA1), (0x2CEB0, 0x2EBE0), (0x2F800, 0x2FA1D),
]


def is_han(char: str) -> bool:
    code = ord(char)
    for lo, hi in _HAN_RANGES:
        if lo <= code <= hi:
            return True
    for lo, hi in _HAN_RANGES_32:
        if lo <= code <= hi:
            return True
    return False


def tokenize(value: str) -> list[str]:
    value = value.lower()
    result: list[str] = []
    word: list[str] = []
    han: list[str] = []

    def flush_word() -> None:
        if len(word) > 1 or (len(word) == 1 and word[0].isdecimal()):
            result.append("".join(word))
        word.clear()

    def flush_han() -> None:
        for idx, current in enumerate(han):
            result.append(current)
            if idx + 1 < len(han):
                result.append(current + han[idx + 1])
        han.clear()

    for current in value:
        if is_han(current):
            flush_word()
            han.append(current)
        elif current.isalpha() or current.isdecimal() or current == "_" or current == "-":
            flush_han()
            word.append(current)
        else:
            flush_word()
            flush_han()
    flush_word()
    flush_han()
    return result


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def normalize_for_phrase(value: str) -> str:
    return "".join(c for c in value.lower() if c.isalpha() or c.isdecimal())
