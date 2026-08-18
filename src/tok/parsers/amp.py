"""Sourcegraph Amp CLI local usage parser.

Targeted path / schema (researched 2026-08, Amp + ccusage/amp + OpenUsage):

Default roots (under ``$TOK_HOME`` or ``Path.home()``)::

    ~/.amp
    ~/.config/amp
    ~/.local/share/amp          # official XDG data dir on Linux (2026)

Official extra roots:
    ``$AMP_DATA_DIR``
    ``$XDG_DATA_HOME/amp`` (when ``TOK_HOME`` is unset or contains it)

Primary layout::

    <data_dir>/threads/T-<uuid>.json    # one thread per file
    <data_dir>/ledger.jsonl             # optional credit ledger

Thread JSON (ccusage/amp 2026)::

    {
      "id": "T-<uuid>",
      "created": 1700000000000,          # unix ms
      "messages": [
        {
          "role": "assistant",
          "usage": {
            "model": "claude-haiku-4-5-20251001",
            "inputTokens": 100,
            "outputTokens": 50,
            "cacheCreationInputTokens": 500,
            "cacheReadInputTokens": 200,
            "credits": 1.5
          }
        }
      ]
    }

Only ``role == "assistant"`` (or messages with a ``usage`` object) emit
events. Input is *not* treated as cache-inclusive — Amp records cache
create/read as separate fields.

``credits`` are Amp billing credits, **not** USD. They are stored on
``extra["credits"]``; ``raw_cost_usd`` is only set when a field is clearly
named ``cost`` / ``costUsd``.

Assumed aliases (OpenUsage field-tolerance): snake_case and camelCase
``input`` / ``input_tokens`` / ``inputTokens``,
``cache_read`` / ``cacheReadInputTokens``, etc.

``history.jsonl`` is prompt history and is skipped. ``secrets.json`` is
never opened. Message bodies are ignored.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tok.models import UsageEvent
from tok.parsers._common import (
    as_float,
    extract_cost_usd,
    extract_tokens,
    file_mtime,
    iter_files,
    iter_json_objects,
    make_event,
    parse_timestamp,
    pick,
    resolve_tool_roots,
    safe_model,
    safe_project,
    safe_session_id,
)

TOOL = "amp"


def parse(root: Path | None = None) -> list[UsageEvent]:
    """Parse Amp thread JSON / ledger. Returns ``[]`` if nothing usable is found."""
    roots = resolve_tool_roots(
        root,
        ".amp",
        ".config/amp",
        ".local/share/amp",
        env_vars=("AMP_DATA_DIR",),
        undotted_name="amp",
    )

    events: list[UsageEvent] = []
    ledger_paths: list[Path] = []
    for path in iter_files(roots):
        name = path.name.lower()
        if name in {"history.jsonl", "history.json"}:
            # Prompt history only — no reliable per-turn usage, and it
            # contains user text. Skip entirely.
            continue
        try:
            if name in {"ledger.jsonl", "ledger.json"}:
                ledger_paths.append(path)
            else:
                events.extend(_parse_thread_or_generic(path))
        except OSError:
            continue
    # Ledger is a fallback so we do not double-count thread usage.
    if not events:
        for path in ledger_paths:
            try:
                events.extend(_parse_ledger(path))
            except OSError:
                continue
    return events


def _parse_thread_or_generic(path: Path) -> list[UsageEvent]:
    events: list[UsageEvent] = []
    for obj in iter_json_objects(path):
        if not isinstance(obj, dict):
            continue
        events.extend(_events_from_thread(path, obj))
    return events


def _events_from_thread(path: Path, obj: dict[str, Any]) -> list[UsageEvent]:
    thread_id = _thread_id(obj, path)
    project = safe_project(obj)
    thread_ts = parse_timestamp(
        pick(obj, "created", "created_at", "createdAt", "timestamp"),
        file_mtime(path),
    )
    messages = obj.get("messages")
    events: list[UsageEvent] = []
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            ev = _event_from_message(
                path, msg, thread_id=thread_id, project=project, fallback_ts=thread_ts
            )
            if ev is not None:
                events.append(ev)
        if events:
            events.extend(_events_from_usage_ledger(path, obj, thread_id, project, thread_ts))
            return events

    # Generic usage object / single-message file.
    ev = _event_from_message(
        path, obj, thread_id=thread_id, project=project, fallback_ts=thread_ts
    )
    return [ev] if ev is not None else []


def _events_from_usage_ledger(
    path: Path,
    obj: dict[str, Any],
    thread_id: str | None,
    project: str | None,
    fallback_ts,
) -> list[UsageEvent]:
    """Optional ``usageLedger.events`` — only if they add info not in messages.

    We skip these by default when messages already carried usage, to avoid
    double-counting. Kept as a hook for ledger-only threads.
    """
    # Intentionally unused: message-level usage is authoritative.
    return []


def _event_from_message(
    path: Path,
    msg: dict[str, Any],
    *,
    thread_id: str | None,
    project: str | None,
    fallback_ts,
) -> UsageEvent | None:
    role = msg.get("role") or msg.get("type")
    if role in {"user", "system", "info"}:
        return None
    usage = msg.get("usage")
    src = usage if isinstance(usage, dict) else msg
    tokens = extract_tokens(src if isinstance(src, dict) else msg)
    if not any(tokens.values()) and not isinstance(usage, dict):
        return None

    credits = None
    if isinstance(src, dict):
        credits = as_float(src.get("credits"))
    if credits is None:
        credits = as_float(msg.get("credits"))

    extra: dict[str, Any] = {"format": "amp-thread"}
    if credits is not None:
        extra["credits"] = credits

    # raw_cost_usd only when clearly USD-named (not Amp credits).
    cost = None
    if isinstance(src, dict):
        if any(k in src for k in ("cost_usd", "costUsd", "raw_cost_usd")):
            cost = extract_cost_usd(src)

    ts = parse_timestamp(
        pick(msg, "timestamp", "created_at", "createdAt", "usageTimestamp"),
        fallback_ts,
    )
    model = safe_model(src if isinstance(src, dict) else {}) or safe_model(msg)
    return make_event(
        TOOL,
        path,
        src if isinstance(src, dict) else msg,
        timestamp=ts,
        model=model or None,
        project=project,
        session_id=thread_id or safe_session_id(msg),
        extra=extra,
        tokens=tokens if any(tokens.values()) else None,
        raw_cost_usd=cost,
        inclusive_input=False,
    )


def _parse_ledger(path: Path) -> list[UsageEvent]:
    events: list[UsageEvent] = []
    for obj in iter_json_objects(path):
        if not isinstance(obj, dict):
            continue
        tokens = extract_tokens(obj)
        inner = obj.get("tokens")
        if isinstance(inner, dict) and not any(tokens.values()):
            tokens = extract_tokens(inner)
        credits = as_float(obj.get("credits") or obj.get("cost"))
        extra: dict[str, Any] = {"format": "amp-ledger"}
        if credits is not None:
            extra["credits"] = credits
        ev = make_event(
            TOOL,
            path,
            obj,
            timestamp=parse_timestamp(
                pick(obj, "timestamp", "created_at", "createdAt"),
                file_mtime(path),
            ),
            model=safe_model(obj) or None,
            project=safe_project(obj),
            session_id=safe_session_id(obj) or _thread_id(obj, path),
            extra=extra,
            tokens=tokens if any(tokens.values()) else None,
            raw_cost_usd=None,  # credits, not USD
        )
        if ev is not None:
            events.append(ev)
    return events


def _thread_id(obj: dict[str, Any], path: Path) -> str | None:
    ident = obj.get("id") or obj.get("thread_id") or obj.get("threadId")
    if isinstance(ident, str) and ident.strip():
        return ident.strip()
    name = path.stem
    if name.startswith("T-") or name.startswith("t-"):
        return name
    return safe_session_id(obj)
