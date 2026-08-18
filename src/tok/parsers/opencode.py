"""OpenCode (sst/anomalyco) local usage parser.

Targeted path / schema (researched 2026-08, opencode + ccusage +
opencode-usage-cli):

Default roots (under ``$TOK_HOME`` or ``Path.home()``)::

    ~/.opencode
    ~/.config/opencode
    ~/.local/share/opencode

Official extra root: ``$OPENCODE_DATA_DIR`` (comma-separated).
SQLite override: ``$OPENCODE_DB``.

Two generations of local data:

1. **Legacy JSON** (pre-2.0, still common)::

       ~/.local/share/opencode/storage/
           session/{projectID}/{sessionID}.json
           message/{sessionID}/{messageID}.json

   Assistant message (MessageV2 / older metadata.assistant wrapper)::

       role: "assistant"
       modelID / providerID
       cost                  # USD
       tokens.input
       tokens.output
       tokens.reasoning
       tokens.cache.read
       tokens.cache.write
       time.created          # unix ms

2. **SQLite** (OpenCode v2, 2026+) — ``opencode.db`` table ``session``::

       tokens_input, tokens_output, tokens_reasoning,
       tokens_cache_read, tokens_cache_write, cost, model, time_created,
       directory, id

Message-level events are preferred. Session rollups (session JSON or the
SQLite ``session`` row) are emitted only when no per-message usage was
found for that session id, so totals are not double-counted.

Assumed aliases: camelCase ``inputTokens``, nested ``metadata.assistant``,
``session_message.data`` JSON blobs (tokens extracted; text dropped).

Part files under ``storage/part/`` hold message *content* and are skipped.
``auth.json`` is never opened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tok.models import UsageEvent
from tok.parsers._common import (
    env_roots,
    extract_tokens,
    file_mtime,
    is_secret_path,
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

TOOL = "opencode"


def parse(root: Path | None = None) -> list[UsageEvent]:
    """Parse OpenCode JSON storage and/or SQLite. Returns ``[]`` if nothing found."""
    roots = resolve_tool_roots(
        root,
        ".opencode",
        ".config/opencode",
        ".local/share/opencode",
        env_vars=("OPENCODE_DATA_DIR",),
        undotted_name="opencode",
    )
    if root is None:
        roots.extend(env_roots("OPENCODE_DB"))

    message_events: list[UsageEvent] = []
    session_events: list[UsageEvent] = []

    for path in iter_files(roots, extra_suffixes=(".db", ".sqlite", ".sqlite3")):
        try:
            suffix = path.suffix.lower()
            if suffix in {".db", ".sqlite", ".sqlite3"} or path.name == "opencode.db":
                sess, msgs = _parse_sqlite(path)
                session_events.extend(sess)
                message_events.extend(msgs)
                continue
            if _is_part_file(path):
                continue
            kind, evs = _parse_json_file(path)
            if kind == "message":
                message_events.extend(evs)
            else:
                session_events.extend(evs)
        except OSError:
            continue

    return _dedupe(message_events, session_events)


def _is_part_file(path: Path) -> bool:
    # storage/part/{messageID}/{partID}.json — content, not usage.
    return "part" in path.parts and path.suffix == ".json"


def _parse_json_file(path: Path) -> tuple[str, list[UsageEvent]]:
    events: list[UsageEvent] = []
    kind = "session"
    for obj in iter_json_objects(path):
        if not isinstance(obj, dict):
            continue
        k, ev = _event_from_opencode_obj(path, obj)
        if ev is None:
            continue
        if k == "message":
            kind = "message"
        events.append(ev)
    return kind, events


def _event_from_opencode_obj(
    path: Path, obj: dict[str, Any]
) -> tuple[str, UsageEvent | None]:
    role = obj.get("role")
    assistant = obj.get("assistant")
    metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    if assistant is None and isinstance(metadata.get("assistant"), dict):
        assistant = metadata["assistant"]

    kind = "message" if role == "assistant" or isinstance(assistant, dict) else "session"
    if role in {"user", "system"}:
        return "message", None

    src = assistant if isinstance(assistant, dict) else obj
    # Flatten tokens from either layout onto src for extract_tokens.
    tokens = extract_tokens(src)
    if not any(tokens.values()):
        tokens = extract_tokens(obj)

    model = safe_model(src) or safe_model(obj)
    if isinstance(src, dict) and src.get("providerID") and src.get("modelID"):
        model = f"{src['providerID']}/{src['modelID']}"
    elif isinstance(src, dict) and src.get("modelID"):
        model = str(src["modelID"])

    ts = parse_timestamp(
        pick(src, "time", "created", "time.created")
        or (src.get("time") if isinstance(src, dict) else None)
        or pick(obj, "time", "created_at", "time_created")
        or obj.get("time"),
        file_mtime(path),
    )
    session_id = (
        safe_session_id(obj)
        or safe_session_id(metadata)
        or _session_from_path(path)
    )
    project = safe_project(obj) or safe_project(metadata) or _project_from_path(path)

    extra = {"format": "opencode-json", "level": kind}
    ev = make_event(
        TOOL,
        path,
        src if isinstance(src, dict) else obj,
        timestamp=ts,
        model=model or None,
        project=project,
        session_id=session_id,
        extra=extra,
        tokens=tokens if any(tokens.values()) else None,
        raw_cost_usd=_cost_from(src, obj),
    )
    return kind, ev


def _cost_from(*objs: Any) -> float | None:
    from tok.parsers._common import extract_cost_usd

    for obj in objs:
        if isinstance(obj, dict):
            cost = extract_cost_usd(obj)
            if cost is not None:
                return cost
    return None


def _session_from_path(path: Path) -> str | None:
    # storage/message/{sessionID}/{messageID}.json
    # storage/session/{projectID}/{sessionID}.json
    parts = path.parts
    if "message" in parts:
        i = parts.index("message")
        if i + 1 < len(parts):
            return parts[i + 1]
    if "session" in parts:
        stem = path.stem
        if stem:
            return stem
    return None


def _project_from_path(path: Path) -> str | None:
    parts = path.parts
    if "session" in parts:
        i = parts.index("session")
        if i + 1 < len(parts) and parts[i + 1] != path.name:
            return parts[i + 1]
    return None


def _parse_sqlite(path: Path) -> tuple[list[UsageEvent], list[UsageEvent]]:
    """Return (session_events, message_events)."""
    if is_secret_path(path):
        return [], []
    try:
        import sqlite3
    except ImportError:
        return [], []
    try:
        uri = path.resolve().as_posix()
        conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except Exception:
        return [], []

    session_events: list[UsageEvent] = []
    message_events: list[UsageEvent] = []
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "session" in tables or "sessions" in tables:
            table = "session" if "session" in tables else "sessions"
            session_events.extend(_sqlite_session_rows(conn, table, path))
        for table in ("message", "messages", "session_message"):
            if table in tables:
                message_events.extend(_sqlite_message_rows(conn, table, path))
    except Exception:
        return session_events, message_events
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return session_events, message_events


def _row_keys(row: Any) -> set[str]:
    try:
        return set(row.keys())
    except Exception:
        return set()


def _sqlite_session_rows(conn: Any, table: str, path: Path) -> list[UsageEvent]:
    try:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    except Exception:
        return []
    events: list[UsageEvent] = []
    for row in rows:
        keys = _row_keys(row)
        data = {k: row[k] for k in keys}
        # Prefer dedicated token columns; fall back to JSON ``data``.
        tokens = {
            "input_tokens": int(data.get("tokens_input") or data.get("prompt_tokens") or 0),
            "output_tokens": int(data.get("tokens_output") or data.get("completion_tokens") or 0),
            "cache_read_tokens": int(data.get("tokens_cache_read") or 0),
            "cache_write_tokens": int(data.get("tokens_cache_write") or 0),
            "reasoning_tokens": int(data.get("tokens_reasoning") or 0),
        }
        if not any(tokens.values()) and isinstance(data.get("data"), str):
            tokens = _tokens_from_json_blob(data["data"])
        model = _model_from_sqlite(data.get("model"))
        ts = parse_timestamp(
            data.get("time_created") or data.get("created_at") or data.get("time"),
            file_mtime(path),
        )
        project = None
        for key in ("directory", "path", "project_id", "projectID"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                project = Path(val).name or val
                break
        cost = data.get("cost")
        try:
            raw_cost = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            raw_cost = None
        sid = data.get("id") or data.get("session_id")
        ev = make_event(
            TOOL,
            path,
            {"model": model} if model else {},
            timestamp=ts,
            model=model or None,
            project=project,
            session_id=str(sid) if sid else None,
            extra={"format": "opencode-sqlite", "level": "session"},
            tokens=tokens,
            raw_cost_usd=raw_cost,
        )
        if ev is not None:
            events.append(ev)
    return events


def _sqlite_message_rows(conn: Any, table: str, path: Path) -> list[UsageEvent]:
    # Only pull columns that are usage metadata. If a JSON ``data`` blob
    # exists we parse tokens out of it and discard any text fields.
    try:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    except Exception:
        return []
    events: list[UsageEvent] = []
    for row in rows:
        keys = _row_keys(row)
        data = {k: row[k] for k in keys}
        blob = data.get("data")
        obj: dict[str, Any] = {}
        if isinstance(blob, str) and blob.startswith("{"):
            obj = _json_object(blob)
            # Drop content-bearing keys before any further use.
            for bad in ("parts", "content", "text", "system"):
                obj.pop(bad, None)
        tokens = extract_tokens(obj) if obj else {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
        }
        if not any(tokens.values()):
            continue
        assistant = obj.get("assistant") if isinstance(obj.get("assistant"), dict) else obj
        ev = make_event(
            TOOL,
            path,
            assistant if isinstance(assistant, dict) else obj,
            timestamp=parse_timestamp(
                data.get("time_created") or obj.get("time"),
                file_mtime(path),
            ),
            model=safe_model(assistant if isinstance(assistant, dict) else obj) or None,
            project=safe_project(obj),
            session_id=str(data.get("session_id") or obj.get("sessionID") or "") or None,
            extra={"format": "opencode-sqlite", "level": "message"},
            tokens=tokens,
        )
        if ev is not None:
            events.append(ev)
    return events


def _tokens_from_json_blob(blob: str) -> dict[str, int]:
    obj = _json_object(blob)
    if not obj:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
        }
    return extract_tokens(obj)


def _json_object(blob: str) -> dict[str, Any]:
    import json

    try:
        val = json.loads(blob)
    except json.JSONDecodeError:
        return {}
    return val if isinstance(val, dict) else {}


def _model_from_sqlite(value: Any) -> str:
    if isinstance(value, str) and value.startswith("{"):
        obj = _json_object(value)
        return safe_model(obj, default="") or ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return safe_model(value, default="")
    return ""


def _dedupe(
    message_events: list[UsageEvent], session_events: list[UsageEvent]
) -> list[UsageEvent]:
    """Drop session rollups when we already have per-message rows."""
    msg_sessions = {e.session_id for e in message_events if e.session_id}
    kept_sessions = [
        e
        for e in session_events
        if not e.session_id or e.session_id not in msg_sessions
    ]
    return message_events + kept_sessions
