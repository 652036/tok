"""Parse Claude Code local token-usage logs.

Claude Code stores append-only JSONL transcripts at::

    ~/.config/claude/projects/<encoded-cwd>/<session-id>.jsonl
    ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl

and sometimes extra JSONL under ``stats/``.  This module walks each
root recursively for ``*.jsonl`` / ``*.json``.

Default roots (under ``$TOK_HOME`` or ``Path.home()``)::

    ~/.config/claude     # XDG; ccusage checks this first
    ~/.claude            # legacy / official Windows %USERPROFILE%/.claude

``$CLAUDE_CONFIG_DIR`` (comma-separated) is honoured in addition.

Record schema (verified against ccusage's usageDataSchema and Claude Code
JSONL docs, 2026):

    {
      "type": "assistant" | "user" | ...,
      "timestamp": "2026-03-01T12:00:00.000Z",   # ISO-8601
      "sessionId": "<uuid>",
      "cwd": "/abs/project",
      "gitBranch": "main",                       # ignored (not stored)
      "version": "2.1.0",
      "costUSD": 0.012,                          # optional; often 0
      "message": {
        "model": "claude-sonnet-4-20250514",
        "usage": {
          "input_tokens": 100,
          "output_tokens": 50,
          "cache_read_input_tokens": 1000,
          "cache_creation_input_tokens": 200
        }
      }
    }

Usage may also sit at the top level (``usage``) on some stats rollups.
Records without a usage object are skipped.

Project folder names are the working directory with non-alphanumeric
characters replaced by ``-`` (``/home/me/app`` → ``-home-me-app``).
Some tooling URL-encodes the path instead (``%2Fhome%2Fme%2Fapp``).
``cwd`` on the record is preferred; otherwise the ``projects/<encoded>``
directory name is URL-decoded (dash-encoding is lossy and only used as
a fallback).

Only usage metadata is kept.  Message content, API keys, cookies, and
authorization headers are never copied into :class:`UsageEvent.extra`.
``extra`` is limited to ``cwd`` and ``version``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from tok.models import UsageEvent
from tok.parsers import _common

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "cookies",
        "password",
        "secret",
        "token",
        "access_token",
    }
)
_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def parse(root: Path | None = None) -> list[UsageEvent]:
    """Return usage events under ``root``.

    ``root`` may be omitted (``$TOK_HOME`` / ``~`` plus ``.config/claude``
    and ``.claude``, plus ``$CLAUDE_CONFIG_DIR``), a home directory that
    contains those dirs, the Claude data dir itself, or a single
    JSON/JSONL file.
    """
    roots = _common.resolve_tool_roots(
        root,
        ".config/claude",
        ".claude",
        env_vars=("CLAUDE_CONFIG_DIR",),
        undotted_name="claude",
    )
    events: list[UsageEvent] = []
    for path in _common.iter_files(roots, suffixes=(".jsonl", ".json")):
        events.extend(_parse_file(path))
    events.sort(key=lambda e: e.timestamp)
    return events


def _parse_file(path: Path) -> Iterator[UsageEvent]:
    if _common.is_secret_path(path):
        return
    for record in _iter_records(path):
        event = _record_to_event(record, path)
        if event is not None:
            yield event


def _iter_records(path: Path) -> Iterator[dict[str, Any]]:
    for data in _common.iter_json_objects(path):
        if isinstance(data, dict):
            yield data
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield item


def _record_to_event(record: dict[str, Any], path: Path) -> UsageEvent | None:
    usage = _find_usage(record)
    if usage is None:
        return None
    tokens = _tokens_from_usage(usage)
    if tokens is None:
        return None
    timestamp = _parse_timestamp(record.get("timestamp") or record.get("ts"))
    if timestamp is None:
        return None

    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    model = (
        _as_str(message.get("model"))
        or _as_str(record.get("model"))
        or "unknown"
    )
    cwd = _as_str(record.get("cwd"))
    version = _as_str(record.get("version"))
    session_id = (
        _as_str(record.get("sessionId") or record.get("session_id"))
        or _session_from_filename(path)
    )
    project = cwd or _project_from_path(path)
    raw_cost = _as_float(record.get("costUSD", record.get("cost_usd")))

    return UsageEvent(
        tool="claude",
        timestamp=timestamp,
        model=model,
        project=project,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cache_read_tokens=tokens[2],
        cache_write_tokens=tokens[3],
        reasoning_tokens=tokens[4],
        raw_cost_usd=raw_cost,
        source_path=str(path),
        session_id=session_id,
        extra=_safe_extra(cwd=cwd, version=version),
    )


def _find_usage(record: dict[str, Any]) -> dict[str, Any] | None:
    for candidate in (
        record.get("usage"),
        record.get("message", {}).get("usage")
        if isinstance(record.get("message"), dict)
        else None,
    ):
        if isinstance(candidate, dict) and _has_usage_keys(candidate):
            return candidate
    return None


def _has_usage_keys(obj: dict[str, Any]) -> bool:
    return any(key in obj for key in _USAGE_KEYS)


def _tokens_from_usage(usage: dict[str, Any]) -> tuple[int, int, int, int, int] | None:
    inp = _as_int(usage.get("input_tokens"))
    out = _as_int(usage.get("output_tokens"))
    cache_read = _as_int(
        usage.get("cache_read_input_tokens", usage.get("cache_read_tokens"))
    )
    cache_write = _as_int(
        usage.get("cache_creation_input_tokens", usage.get("cache_write_tokens"))
    )
    reasoning = _as_int(usage.get("reasoning_tokens"))
    if inp == out == cache_read == cache_write == reasoning == 0:
        return None
    return inp, out, cache_read, cache_write, reasoning


def _project_from_path(path: Path) -> str | None:
    parts = path.parts
    try:
        idx = parts.index("projects")
    except ValueError:
        return None
    if idx + 1 >= len(parts):
        return None
    encoded = parts[idx + 1]
    if encoded.endswith(".jsonl") or encoded.endswith(".json"):
        return None
    return _decode_project_dir(encoded)


def _decode_project_dir(name: str) -> str:
    decoded = unquote(name)
    # Claude Code replaces non-alphanumeric chars (including "/") with "-".
    # That mapping is lossy; only apply it when the name still looks encoded.
    if decoded.startswith("-") and "/" not in decoded:
        return "/" + decoded[1:].replace("-", "/")
    return decoded


def _session_from_filename(path: Path) -> str | None:
    match = _UUID_RE.search(path.stem)
    if match:
        return match.group(0)
    stem = path.stem
    return stem or None


def _safe_extra(*, cwd: str | None, version: str | None) -> dict[str, str]:
    extra: dict[str, str] = {}
    if cwd:
        extra["cwd"] = cwd
    if version:
        extra["version"] = version
    for key in list(extra):
        if key.lower() in _SECRET_KEYS:
            extra.pop(key, None)
    return extra


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


# Imported by tests that want to reuse helpers without scraping modules.
__all__ = ["parse"]
