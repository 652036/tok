"""Grok Build CLI (xAI) local usage parser.

Targeted path / schema (researched 2026-08, verified on disk 2026-09):

Default roots (under ``$TOK_HOME`` or ``Path.home()``):
    ~/.grok
    ~/.config/grok
    ~/.xai

Official extra root: ``$GROK_HOME`` (xAI default is ``~/.grok``).

Primary layout::

    $GROK_HOME/sessions/<url-encoded-cwd>/<session-uuid>/
        updates.jsonl   # PRIMARY — ACP session updates (turn_completed usage)
        usage.json      # grok usage persist: session + turns (preferred if present)
        summary.json    # optional metadata (model, cwd, timestamps)
        signals.json    # optional context counters (not billed usage)
        chat_history.jsonl  # conversation; no billed keys; not opened

``updates.jsonl`` rows we accept (verified on local Grok Build sessions, 2026-09):

* ACP envelope: ``{timestamp: int unix-seconds, method, params}``
* ``params.update.sessionUpdate == "turn_completed"``
* billed usage at ``params.update.usage`` (not on chat_history.jsonl)::

      inputTokens / outputTokens / cachedReadTokens /
      cacheCreationTokens / reasoningTokens / costUsdTicks /
      modelUsage.<model-id>.{same keys}

* ``modelUsage`` may sit on the update row *or* nested under ``usage``
* ``costUsdTicks``: integer ticks of **1e-10 USD**

``usage.json`` (``grok usage`` persist, same session dir) is the per-turn
envelope ``{sessionId, updatedAt, session, turns[]}`` with the same token
keys on each turn. When a sibling ``usage.json`` has a non-empty ``turns``
list, ``updates.jsonl`` in that directory is skipped to avoid double-count.

``params._meta.totalTokens`` and ``signals.json`` ``contextTokensUsed`` are
running context size, not billed I/O — ignored.

Assumed aliases (official schema still drifting; parser is resilient):
    input_tokens, prompt_tokens, cache_read, cacheReadInputTokens,
    cost_usd, timestamp / created_at / endedAt, model / current_model_id /
    primaryModelId.

``inputTokens`` is treated as cache-inclusive (uncached = input − cache read)
when input >= cache read, matching ccusage.

No message content or API keys are copied into events. Credential files
(``auth.json``, ``mcp_credentials.json``) are never opened. Conversation
files (``chat_history.jsonl``) are not opened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tok.models import UsageEvent
from tok.parsers._common import (
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
    sibling_json,
)

TOOL = "grok"

# Metadata / conversation / noise. Never opened (summary/signals are loaded
# via sibling_json when parsing billed files).
_SKIP_NAMES = {
    "summary.json",
    "signals.json",
    "chat_history.jsonl",
    "events.jsonl",
    "prompt_context.json",
    "rewind_points.jsonl",
    "plan.json",
    "plan_mode.json",
    "announcement_state.json",
    "resources_state.json",
    "hunk_records.jsonl",
    "unified.jsonl",
}

# Grok home subtrees that are not session usage (skills, caches, debug logs).
_NOISE_DIR_NAMES = {
    "bundled",
    "downloads",
    "marketplace-cache",
    "installed-plugins",
    "skills",
    "bin",
    "vendor",
    "debug",
    "completions",
    "relocations",
    "memtrace",
    "worktrees",
    "docs",
}


def parse(root: Path | None = None) -> list[UsageEvent]:
    """Parse Grok Build session logs. Returns ``[]`` if nothing usable is found."""
    roots = resolve_tool_roots(
        root,
        ".grok",
        ".config/grok",
        ".xai",
        env_vars=("GROK_HOME",),
        undotted_name="grok",
    )
    events: list[UsageEvent] = []
    for path in iter_files(roots):
        if _is_noise_path(path):
            continue
        try:
            events.extend(_parse_file(path))
        except OSError:
            continue
    return events


def _is_noise_path(path: Path) -> bool:
    name = path.name.lower()
    if name in _SKIP_NAMES:
        return True
    return any(part.lower() in _NOISE_DIR_NAMES for part in path.parts)


def _parse_file(path: Path) -> list[UsageEvent]:
    name = path.name.lower()
    if name in _SKIP_NAMES:
        return []
    # Prefer grok-usage turns over the same session's ACP stream.
    if name == "updates.jsonl" and _sibling_usage_has_turns(path):
        return []

    meta = sibling_json(path, "summary.json", "signals.json")
    session_fallback = _session_id_from_path(path, meta)
    project_fallback = safe_project(meta) if meta else _project_from_path(path)
    model_fallback = safe_model(meta, default="") if meta else ""
    ts_fallback = parse_timestamp(
        pick(meta, "last_active_at", "updated_at", "created_at", "timestamp") if meta else None,
        file_mtime(path),
    )

    events: list[UsageEvent] = []
    for obj in iter_json_objects(path):
        if not isinstance(obj, dict):
            continue
        events.extend(
            _events_from_obj(
                path,
                obj,
                session_id=session_fallback,
                project=project_fallback,
                model=model_fallback or None,
                timestamp=ts_fallback,
            )
        )
    return events


def _events_from_obj(
    path: Path,
    obj: dict[str, Any],
    *,
    session_id: str | None,
    project: str | None,
    model: str | None,
    timestamp,
    extra_format: str | None = None,
) -> list[UsageEvent]:
    if extra_format is None:
        envelope = _events_from_usage_envelope(
            path,
            obj,
            session_id=session_id,
            project=project,
            model=model,
            timestamp=timestamp,
        )
        if envelope is not None:
            return envelope

    layers = _flatten_layers(obj)
    kind = _session_update(layers)
    # Prefer turn_completed; still accept any usage-bearing row (best-effort).
    model_usage = _first_dict(layers, "modelUsage", "model_usage")
    usage = _first_usage(layers)
    extra = {"format": extra_format or "grok-updates"}
    if kind and extra["format"] == "grok-updates":
        extra["session_update"] = kind

    ts = parse_timestamp(
        pick(obj, "timestamp", "created_at", "createdAt", "endedAt", "updatedAt", "updated_at"),
        timestamp,
    )
    sid = session_id
    for layer in layers:
        found = safe_session_id(layer, None)
        if found:
            sid = found
            break
    proj = safe_project(obj, project)
    model_name = None
    for layer in layers:
        found_model = safe_model(layer, default="")
        if found_model:
            model_name = found_model
            break

    cost = None
    for layer in layers:
        cost = extract_cost_usd(layer)
        if cost is not None:
            break

    out: list[UsageEvent] = []
    if isinstance(model_usage, dict) and model_usage:
        for model_id, breakdown in model_usage.items():
            if not isinstance(model_id, str) or not model_id.strip():
                continue
            src = breakdown if isinstance(breakdown, dict) else (usage or obj)
            if not isinstance(src, dict):
                continue
            ev = make_event(
                TOOL,
                path,
                src,
                timestamp=ts,
                model=model_id.strip(),
                project=proj,
                session_id=sid,
                extra=extra,
                inclusive_input=True,
                raw_cost_usd=extract_cost_usd(src) if isinstance(breakdown, dict) else cost,
            )
            if ev is not None:
                out.append(ev)
        if out:
            return out
    src = usage or obj
    ev = make_event(
        TOOL,
        path,
        src if isinstance(src, dict) else obj,
        timestamp=ts,
        model=model_name or model or None,
        project=proj,
        session_id=sid,
        extra=extra,
        inclusive_input=True,
        raw_cost_usd=cost,
    )
    return [ev] if ev is not None else []


def _events_from_usage_envelope(
    path: Path,
    obj: dict[str, Any],
    *,
    session_id: str | None,
    project: str | None,
    model: str | None,
    timestamp,
) -> list[UsageEvent] | None:
    """Parse ``grok usage`` JSON: ``{sessionId, updatedAt, session, turns}``.

    Returns ``None`` when *obj* is not that envelope so the caller can fall
    through to ACP / flat usage objects.
    """
    if not _is_usage_envelope(obj):
        return None
    sid = safe_session_id(obj, session_id)
    envelope_model = safe_model(obj, default="") or model
    envelope_ts = parse_timestamp(
        pick(obj, "updatedAt", "updated_at", "timestamp", "endedAt"),
        timestamp,
    )
    turns = obj.get("turns")
    out: list[UsageEvent] = []
    if isinstance(turns, list):
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            out.extend(
                _events_from_obj(
                    path,
                    turn,
                    session_id=sid,
                    project=project,
                    model=safe_model(turn, default="") or envelope_model,
                    timestamp=envelope_ts,
                    extra_format="grok-usage",
                )
            )
        if out:
            return out
    session = obj.get("session")
    if isinstance(session, dict):
        return _events_from_obj(
            path,
            session,
            session_id=sid,
            project=project,
            model=safe_model(session, default="") or envelope_model,
            timestamp=envelope_ts,
            extra_format="grok-usage",
        )
    return []


def _is_usage_envelope(obj: dict[str, Any]) -> bool:
    turns = obj.get("turns")
    session = obj.get("session")
    if not isinstance(turns, list):
        return False
    if isinstance(session, dict):
        return True
    return any(k in obj for k in ("sessionId", "session_id", "updatedAt", "updated_at"))


def _sibling_usage_has_turns(path: Path) -> bool:
    obj = sibling_json(path, "usage.json")
    turns = obj.get("turns")
    return isinstance(turns, list) and any(isinstance(item, dict) for item in turns)


def _flatten_layers(obj: dict[str, Any]) -> list[dict[str, Any]]:
    layers = [obj]
    cur = obj
    # Chain: params -> update (ACP). Do not require payload/data/result.
    for key in ("params", "update", "payload", "data", "result"):
        nxt = cur.get(key) if isinstance(cur, dict) else None
        if isinstance(nxt, dict):
            layers.append(nxt)
            cur = nxt
        else:
            break
    # Also consider a one-level ``update`` sitting next to ``params``.
    upd = obj.get("update")
    if isinstance(upd, dict) and upd not in layers:
        layers.append(upd)
    # Live billed keys sit at params.update.usage (not a chain step above).
    extra: list[dict[str, Any]] = []
    for layer in layers:
        usage = layer.get("usage")
        if isinstance(usage, dict) and usage not in layers and usage not in extra:
            extra.append(usage)
    layers.extend(extra)
    return layers


def _session_update(layers: list[dict[str, Any]]) -> str | None:
    for layer in layers:
        for key in ("sessionUpdate", "session_update", "type", "event"):
            val = layer.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def _first_dict(layers: list[dict[str, Any]], *keys: str) -> dict[str, Any] | None:
    for layer in layers:
        for key in keys:
            val = layer.get(key)
            if isinstance(val, dict) and val:
                return val
    return None


def _first_usage(layers: list[dict[str, Any]]) -> dict[str, Any] | None:
    found = _first_dict(layers, "usage", "tokens", "token_usage", "tokenUsage")
    if found:
        return found
    for layer in layers:
        tok = extract_tokens(layer)
        if any(tok.values()):
            return layer
    return None


def _session_id_from_path(path: Path, meta: dict[str, Any]) -> str | None:
    sid = safe_session_id(meta) if meta else None
    if sid:
        return sid
    # .../sessions/<cwd>/<session-uuid>/updates.jsonl
    parent = path.parent.name
    if parent and parent not in {"sessions", "logs"}:
        return parent
    return None


def _project_from_path(path: Path) -> str | None:
    # sessions/<url-encoded-cwd>/<uuid>/file
    parts = path.parts
    if "sessions" in parts:
        i = parts.index("sessions")
        if i + 1 < len(parts):
            encoded = parts[i + 1]
            try:
                from urllib.parse import unquote

                decoded = unquote(encoded)
            except Exception:
                decoded = encoded
            if decoded and decoded not in {path.parent.name, path.name}:
                return Path(decoded).name or decoded
    return None
