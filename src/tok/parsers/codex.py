"""Best-effort parser for OpenAI Codex CLI local logs.

Primary on-disk format (Codex CLI / ccusage / tokenuse, 2026)::

    ~/.codex/sessions/YYYY/MM/DD/rollout-<ISO>-<uuid>.jsonl
    ~/.codex/archived_sessions/rollout-*.jsonl

Each line is an envelope ``{timestamp, type, payload}``.

Usage is recorded on ``type == "event_msg"`` rows whose
``payload.type`` is ``token_count``:

    payload.info.last_token_usage / payload.info.total_token_usage
        input_tokens
        cached_input_tokens          # also spelled cache_read_input_tokens
        output_tokens
        reasoning_output_tokens
        total_tokens

``total_token_usage`` is cumulative for the session.  This parser prefers
the cumulative delta (previous total subtracted) so duplicate snapshots
with an unchanged total are not double-counted.  If only
``last_token_usage`` is present, that per-turn object is used.

``turn_context.payload.model`` is the active model.
``session_meta.payload`` carries ``id`` (session) and ``cwd`` (project).

OpenAI reports cached tokens *inside* ``input_tokens``.  Cached tokens
are subtracted from ``input_tokens`` and stored as ``cache_read_tokens``.
Cache-write tokens are not exposed by Codex and stay 0.

Also accepted (older dumps, ``codex exec --json``, ad-hoc exports):

- ``usage.input_tokens`` / ``usage.output_tokens``
- ``token_usage`` object
- ``message.usage`` (Claude-like)
- ``type == "turn.completed"`` / ``turn_completed`` with a ``usage`` object
- flat ``input_tokens`` / ``output_tokens`` on the record

Default ``root`` is ``Path.home() / ".codex"``.  If ``TOK_HOME`` (or ``AI_USAGE_HOME``) is
set, that directory is used as the home and the root becomes
``$TOK_HOME/.codex``.

The walker reads every ``*.json`` / ``*.jsonl`` under ``root``.  Unknown
files, binary junk, and JSON errors are skipped (the parser still
returns a list — never raises on a bad file).  Message content, API
keys, cookies, and authorization headers are never stored.
``UsageEvent.extra`` is limited to ``cwd`` and ``version``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    "cached_input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_output_tokens",
    "reasoning_tokens",
    "input",
    "output",
)
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def parse(root: Path | None = None) -> list[UsageEvent]:
    """Return usage events under ``root``.

    ``root`` may be omitted (``$TOK_HOME/.codex`` or ``~/.codex``),
    a home directory that contains ``.codex/``, the Codex data dir
    itself, or a single JSON/JSONL file.
    """
    resolved = _resolve_root(root)
    if resolved.is_file():
        events = list(_parse_file(resolved))
    elif resolved.is_dir():
        events = []
        for path in _common.iter_files([resolved], suffixes=(".jsonl", ".json")):
            events.extend(_parse_file(path))
    else:
        return []
    events.sort(key=lambda e: e.timestamp)
    return events


def _resolve_root(root: Path | None) -> Path:
    """Accept a home dir, a ``.codex`` data dir, or a single file."""
    if root is None:
        return _common.usage_home() / ".codex"
    root = Path(root)
    if root.is_file():
        return root
    if not root.is_dir():
        return root
    nested = root / ".codex"
    if nested.exists():
        return nested
    undotted = root / "codex"
    if undotted.exists():
        return undotted
    return root


def _parse_file(path: Path) -> list[UsageEvent]:
    if _common.is_secret_path(path):
        return []
    state = _FileState()
    events: list[UsageEvent] = []
    for record in _iter_records(path):
        _absorb_context(record, state)
        event = _record_to_event(record, path, state)
        if event is not None:
            events.append(event)
    return events


class _FileState:
    """Per-file session context for Codex rollout envelopes."""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.cwd: str | None = None
        self.version: str | None = None
        self.model: str | None = None
        self.prev_total: dict[str, int] | None = None


def _absorb_context(record: dict[str, Any], state: _FileState) -> None:
    rec_type = _as_str(record.get("type"))
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}

    if rec_type == "session_meta":
        state.session_id = (
            _as_str(payload.get("id") or payload.get("session_id")) or state.session_id
        )
        state.cwd = _as_str(payload.get("cwd")) or state.cwd
        state.version = _as_str(payload.get("cli_version") or payload.get("version")) or state.version
        return

    if rec_type == "turn_context":
        model = _as_str(payload.get("model"))
        if model:
            state.model = model
        return

    # Non-envelope records may still carry session/model metadata.
    state.session_id = (
        _as_str(record.get("session_id") or record.get("sessionId")) or state.session_id
    )
    state.cwd = _as_str(record.get("cwd")) or state.cwd
    model = _as_str(record.get("model")) or _as_str(payload.get("model"))
    if model:
        state.model = model


def _iter_records(path: Path) -> Iterator[dict[str, Any]]:
    for data in _common.iter_json_objects(path):
        if isinstance(data, dict):
            yield data
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield item


def _record_to_event(
    record: dict[str, Any], path: Path, state: _FileState
) -> UsageEvent | None:
    usage = _extract_usage(record, state)
    if usage is None:
        return None
    tokens = _tokens_from_usage(usage, cached_inside_input=True)
    if tokens is None:
        return None
    timestamp = _parse_timestamp(
        record.get("timestamp") or record.get("ts") or record.get("created_at")
    )
    if timestamp is None:
        return None

    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    model = (
        state.model
        or _as_str(record.get("model"))
        or _as_str(payload.get("model"))
        or _as_str(info.get("model"))
        or "unknown"
    )
    cwd = state.cwd or _as_str(record.get("cwd")) or _as_str(payload.get("cwd"))
    version = state.version or _as_str(record.get("version") or payload.get("cli_version"))
    session_id = (
        state.session_id
        or _as_str(record.get("session_id") or record.get("sessionId") or payload.get("id"))
        or _session_from_filename(path)
    )

    return UsageEvent(
        tool="codex",
        timestamp=timestamp,
        model=model,
        project=cwd,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cache_read_tokens=tokens[2],
        cache_write_tokens=tokens[3],
        reasoning_tokens=tokens[4],
        raw_cost_usd=_as_float(record.get("costUSD", record.get("cost_usd"))),
        source_path=str(path),
        session_id=session_id,
        extra=_safe_extra(cwd=cwd, version=version),
    )


def _extract_usage(record: dict[str, Any], state: _FileState) -> dict[str, Any] | None:
    rec_type = _as_str(record.get("type"))
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else None

    # Canonical Codex rollout: event_msg / token_count.
    if rec_type == "event_msg" and isinstance(payload, dict):
        if _as_str(payload.get("type")) == "token_count":
            return _usage_from_token_count(payload, state)
        return None

    # Nested payload.token_count without the event_msg wrapper.
    if isinstance(payload, dict) and _as_str(payload.get("type")) == "token_count":
        return _usage_from_token_count(payload, state)

    # Known alternate layouts.
    for candidate in _usage_candidates(record, payload):
        if isinstance(candidate, dict) and _has_usage_keys(candidate):
            return candidate
    return None


def _usage_from_token_count(payload: dict[str, Any], state: _FileState) -> dict[str, Any] | None:
    info = payload.get("info")
    if not isinstance(info, dict):
        # Some early dumps put the buckets directly on payload.
        if _has_usage_keys(payload):
            return payload
        return None

    total = info.get("total_token_usage")
    last = info.get("last_token_usage")
    if isinstance(total, dict) and _has_usage_keys(total):
        current = _bucket_ints(total)
        previous = state.prev_total
        state.prev_total = current
        if previous is None:
            return current
        delta = {key: max(0, current[key] - previous.get(key, 0)) for key in current}
        if all(delta[key] == 0 for key in delta):
            return None
        return delta
    if isinstance(last, dict) and _has_usage_keys(last):
        return last
    if _has_usage_keys(info):
        return info
    return None


def _usage_candidates(
    record: dict[str, Any], payload: dict[str, Any] | None
) -> Iterator[Any]:
    yield record.get("usage")
    yield record.get("token_usage")
    message = record.get("message")
    if isinstance(message, dict):
        yield message.get("usage")
    if payload is not None:
        yield payload.get("usage")
        yield payload.get("token_usage")
    # Flat buckets on the record itself (only if it looks like usage, not
    # a whole session_meta blob that happens to reuse a field name).
    if _has_usage_keys(record):
        yield record


def _has_usage_keys(obj: dict[str, Any]) -> bool:
    return any(key in obj for key in _USAGE_KEYS)


def _bucket_ints(usage: dict[str, Any]) -> dict[str, int]:
    return {
        "input_tokens": _as_int(usage.get("input_tokens", usage.get("input"))),
        "output_tokens": _as_int(usage.get("output_tokens", usage.get("output"))),
        "cached_input_tokens": _as_int(
            usage.get(
                "cached_input_tokens",
                usage.get("cache_read_input_tokens", usage.get("cache_read_tokens")),
            )
        ),
        "cache_creation_input_tokens": _as_int(
            usage.get("cache_creation_input_tokens", usage.get("cache_write_tokens"))
        ),
        "reasoning_output_tokens": _as_int(
            usage.get("reasoning_output_tokens", usage.get("reasoning_tokens"))
        ),
    }


def _tokens_from_usage(
    usage: dict[str, Any], *, cached_inside_input: bool
) -> tuple[int, int, int, int, int] | None:
    raw_in = _as_int(usage.get("input_tokens", usage.get("input")))
    out = _as_int(usage.get("output_tokens", usage.get("output")))
    cache_read = _as_int(
        usage.get(
            "cached_input_tokens",
            usage.get("cache_read_input_tokens", usage.get("cache_read_tokens")),
        )
    )
    cache_write = _as_int(
        usage.get("cache_creation_input_tokens", usage.get("cache_write_tokens"))
    )
    reasoning = _as_int(
        usage.get("reasoning_output_tokens", usage.get("reasoning_tokens"))
    )
    if raw_in == out == cache_read == cache_write == reasoning == 0:
        return None
    inp = raw_in
    if cached_inside_input and cache_read:
        inp = max(0, raw_in - cache_read)
    return inp, out, cache_read, cache_write, reasoning


def _session_from_filename(path: Path) -> str | None:
    match = _UUID_RE.search(path.stem)
    if match:
        return match.group(0)
    stem = path.stem
    if stem.startswith("rollout-") and len(stem) > len("rollout-"):
        return stem
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


__all__ = ["parse"]
