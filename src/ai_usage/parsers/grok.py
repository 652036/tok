"""Grok Build CLI (xAI) local usage parser.

Targeted path / schema (researched 2026-08, Grok Build CLI + ccusage):

Default roots (under ``$AI_USAGE_HOME`` or ``Path.home()``):
    ~/.grok
    ~/.config/grok
    ~/.xai

Official extra root: ``$GROK_HOME`` (xAI default is ``~/.grok``).

Primary layout::

    $GROK_HOME/sessions/<url-encoded-cwd>/<session-uuid>/
        updates.jsonl   # PRIMARY — ACP session updates
        summary.json    # optional metadata (model, cwd, timestamps)
        signals.json    # optional context counters (not billed usage)

``updates.jsonl`` rows we accept (several nestings, all observed or assumed):

* ``sessionUpdate == "turn_completed"`` (ccusage 2026 primary path)
* usage on the row, or under ``params.update.usage`` / ``update.usage``
* OpenAI-style token keys (cache-inclusive input)::

      inputTokens / outputTokens / cachedReadTokens /
      cacheCreationTokens / reasoningTokens

* ``modelUsage``: map of model-id → per-model usage breakdown
  (display id e.g. ``grok-4.5-build``)
* ``costUsdTicks``: integer ticks of **1e-10 USD** (ccusage invoice unit)

Assumed aliases (official schema still drifting; parser is resilient):
    input_tokens, prompt_tokens, cache_read, cacheReadInputTokens,
    cost_usd, timestamp / created_at, model / current_model_id.

``inputTokens`` is treated as cache-inclusive (uncached = input − cache read)
when input >= cache read, matching ccusage.

No message content or API keys are copied into events. Credential files
(``auth.json``, ``mcp_credentials.json``) are never opened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_usage.models import UsageEvent
from ai_usage.parsers._common import (
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
        try:
            events.extend(_parse_file(path))
        except OSError:
            continue
    return events


def _parse_file(path: Path) -> list[UsageEvent]:
    name = path.name.lower()
    # summary/signals are metadata only; applied when parsing siblings.
    if name in {"summary.json", "signals.json"}:
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
) -> list[UsageEvent]:
    layers = _flatten_layers(obj)
    kind = _session_update(layers)
    # Prefer turn_completed; still accept any usage-bearing row (best-effort).
    model_usage = _first_dict(layers, "modelUsage", "model_usage")
    usage = _first_usage(layers)
    extra = {"format": "grok-updates"}
    if kind:
        extra["session_update"] = kind

    ts = parse_timestamp(pick(obj, "timestamp", "created_at", "createdAt"), timestamp)
    sid = safe_session_id(obj, session_id)
    proj = safe_project(obj, project)

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
        model=safe_model(obj, default="") or model or None,
        project=proj,
        session_id=sid,
        extra=extra,
        inclusive_input=True,
        raw_cost_usd=cost,
    )
    return [ev] if ev is not None else []


def _flatten_layers(obj: dict[str, Any]) -> list[dict[str, Any]]:
    layers = [obj]
    cur = obj
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
