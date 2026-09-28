"""Environment and .env loading (mirrors Go config.LoadDotEnv semantics)."""
from __future__ import annotations

import os
import re
from pathlib import Path

VERSION = "0.4.1"


def load_env() -> None:
    """Load .env into os.environ. Process env always wins (Go behavior)."""
    path = _resolve_env_path()
    if path is None:
        return
    for key, value in _parse_dotenv(path).items():
        if key not in os.environ:
            os.environ[key] = value


def _resolve_env_path() -> Path | None:
    configured = os.environ.get("OFFERPILOT_CONFIG_PATH", "").strip()
    if configured:
        p = Path(configured)
        return p if p.is_file() else None
    cwd = Path.cwd()
    for d in (cwd, *cwd.parents):
        candidate = d / ".env"
        if candidate.is_file():
            return candidate
    return None


def _parse_dotenv(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = _strip_quotes(value.strip())
        if key:
            result[key] = value
    return result


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def env_or(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


def int_env(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (ValueError, TypeError):
        return default


def bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    if value.lower() in ("1", "true", "t", "yes", "y", "on"):
        return True
    if value.lower() in ("0", "false", "f", "no", "n", "off"):
        return False
    return default


def duration_env(name: str, default: float) -> float:
    """Go duration string ('90s', '1.5s', '2m', '1h', '300ms') -> seconds."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    parsed = _parse_duration(value)
    return parsed if parsed is not None and parsed > 0 else default


_DURATION_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(ms|s|m|h)$")


def _parse_duration(value: str) -> float | None:
    match = _DURATION_RE.fullmatch(value)
    if not match:
        return None
    num = float(match.group(1))
    unit = match.group(2)
    return {"ms": num / 1000.0, "s": num, "m": num * 60.0, "h": num * 3600.0}[unit]


def csv_env(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items if items else default
