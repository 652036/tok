"""Google Gemini CLI local usage parser.

Targeted path / schema (researched 2026-08, gemini-cli + ccusage + tokenuse):

Default roots (under ``$AI_USAGE_HOME`` or ``Path.home()``):
    ~/.gemini
    ~/.config/gemini

Official extra root: ``$GEMINI_DATA_DIR`` (comma-separated; default
``~/.gemini/tmp``).

Primary layout::

    ~/.gemini/tmp/<project_hash>/chats/
        session-*.jsonl   # current (2026) JSONL session format
        session-*.json    # older / exported single-JSON format

JSONL: first line is session metadata (``sessionId``, ``projectHash``,
``startTime``); later lines are per-message records. JSON: the same fields
plus a ``messages`` array.

Per Gemini/model message, official ``TokensSummary``
(``packages/core/src/services/chatRecordingTypes.ts``)::

    tokens.input      # promptTokenCount (cache-inclusive)
    tokens.output     # candidatesTokenCount
    tokens.cached     # cachedContentTokenCount  → cache_read_tokens
    tokens.thoughts   # thoughtsTokenCount       → reasoning_tokens
    tokens.tool       # toolUsePromptTokenCount  (kept in extra only)
    tokens.total
    model, timestamp, type == "gemini"

``input`` includes cached tokens; we store uncached input = input − cached
when input >= cached (ccusage / tokenuse mapping). ``thoughts`` are stored
as ``reasoning_tokens`` and are *not* added on top of ``output_tokens``.

Assumed aliases: ``usage`` / ``usageMetadata`` wrappers, camelCase
``inputTokens``, ``promptTokenCount``.

No message ``content``, thoughts text, or tool arguments are copied.
``oauth_creds.json`` / ``google_accounts.json`` are never opened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_usage.models import UsageEvent
from ai_usage.parsers._common import (
    as_int,
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

TOOL = "gemini"


def parse(root: Path | None = None) -> list[UsageEvent]:
    """Parse Gemini CLI session logs. Returns ``[]`` if nothing usable is found."""
    roots = resolve_tool_roots(
        root,
        ".gemini",
        ".config/gemini",
        env_vars=("GEMINI_DATA_DIR",),
        undotted_name="gemini",
    )
    events: list[UsageEvent] = []
    for path in iter_files(roots):
        try:
            events.extend(_parse_file(path))
        except OSError:
            continue
    return events


def _parse_file(path: Path) -> list[UsageEvent]:
    objects = [obj for obj in iter_json_objects(path)]
    if not objects:
        return []

    session_meta: dict[str, Any] = {}
    messages: list[dict[str, Any]] = []

    for obj in objects:
        if not isinstance(obj, dict):
            continue
        # Session header / single-JSON document.
        if "sessionId" in obj or "session_id" in obj or "projectHash" in obj:
            session_meta = {**session_meta, **{
                k: obj[k]
                for k in obj
                if k not in {"messages", "content", "thoughts", "toolCalls"}
            }}
        if isinstance(obj.get("messages"), list):
            messages.extend(m for m in obj["messages"] if isinstance(m, dict))
            continue
        # JSONL message line (or a standalone usage object).
        if _is_message_or_usage(obj):
            messages.append(obj)

    session_id = safe_session_id(session_meta) or _session_from_filename(path)
    project = safe_project(session_meta) or _project_from_path(path)
    fallback_ts = parse_timestamp(
        pick(session_meta, "startTime", "start_time", "lastUpdated"),
        file_mtime(path),
    )
    fallback_model = safe_model(session_meta, default="")

    events: list[UsageEvent] = []
    for msg in messages:
        ev = _event_from_message(
            path,
            msg,
            session_id=session_id,
            project=project,
            fallback_ts=fallback_ts,
            fallback_model=fallback_model or None,
        )
        if ev is not None:
            events.append(ev)
    return events


def _is_message_or_usage(obj: dict[str, Any]) -> bool:
    typ = obj.get("type") or obj.get("role")
    if typ in {"gemini", "model", "assistant"}:
        return True
    if "tokens" in obj or "usage" in obj or "usageMetadata" in obj:
        return True
    return False


def _event_from_message(
    path: Path,
    msg: dict[str, Any],
    *,
    session_id: str | None,
    project: str | None,
    fallback_ts,
    fallback_model: str | None,
) -> UsageEvent | None:
    typ = msg.get("type") or msg.get("role")
    if typ in {"user", "info", "error", "warning", "system"}:
        return None
    # Rewind / control records have no usage.
    if "$rewindTo" in msg or "$set" in msg:
        return None

    tokens_obj = None
    for key in ("tokens", "usage", "usageMetadata", "usage_metadata"):
        cand = msg.get(key)
        if isinstance(cand, dict):
            tokens_obj = cand
            break
    src = tokens_obj or msg
    tok = extract_tokens(src)
    # Official Gemini keys that extract_tokens already maps: input/output/
    # cached/thoughts. Tool tokens are metadata only.
    tool_tokens = 0
    if isinstance(src, dict):
        tool_tokens = as_int(src.get("tool") or src.get("toolUsePromptTokenCount"))

    extra = {"format": "gemini-session"}
    if tool_tokens:
        extra["tool_tokens"] = tool_tokens

    ts = parse_timestamp(pick(msg, "timestamp", "startTime", "created_at"), fallback_ts)
    return make_event(
        TOOL,
        path,
        src if isinstance(src, dict) else msg,
        timestamp=ts,
        model=safe_model(msg, default="") or fallback_model,
        project=project,
        session_id=session_id or safe_session_id(msg),
        extra=extra,
        inclusive_input=True,
        tokens=tok if any(tok.values()) else None,
    )


def _session_from_filename(path: Path) -> str | None:
    name = path.stem  # session-<uuid>
    if name.startswith("session-"):
        rest = name[len("session-") :]
        return rest or None
    return None


def _project_from_path(path: Path) -> str | None:
    # ~/.gemini/tmp/<project_hash>/chats/session-*.jsonl
    parts = path.parts
    if "tmp" in parts:
        i = parts.index("tmp")
        if i + 1 < len(parts) and parts[i + 1] not in {"chats", path.name}:
            return parts[i + 1]
    if "chats" in parts:
        i = parts.index("chats")
        if i > 0:
            return parts[i - 1]
    return None
