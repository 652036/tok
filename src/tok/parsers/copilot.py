"""GitHub Copilot CLI local usage parser (OpenTelemetry file export).

Targeted path / schema (researched 2026-08, GitHub Copilot OTEL + ccusage
commit 14658ff):

Default roots (under ``$TOK_HOME`` or ``Path.home()``)::

    ~/.copilot
    ~/.config/github-copilot

Official extra file: ``$COPILOT_OTEL_FILE_EXPORTER_PATH`` (single JSONL).

No usage JSONL is written unless file export is enabled before the session::

    COPILOT_OTEL_ENABLED=true
    COPILOT_OTEL_EXPORTER_TYPE=file
    COPILOT_OTEL_FILE_EXPORTER_PATH=...jsonl

Primary layout::

    ~/.copilot/otel/*.jsonl     # OTEL JSON-lines (not a Copilot-specific object)

Prefer **chat** spans (``gen_ai.operation.name == "chat"`` / name prefix
``chat ``). ``invoke_agent`` / inference logs are fallbacks for the same
trace or ``gen_ai.response.id`` and are dropped when a chat span exists.
``execute_tool`` spans and metric rows are ignored.

Confirmed attributes (flat dotted keys, or OTLP ``{key, value}`` lists)::

    gen_ai.request.model / gen_ai.response.model
    gen_ai.usage.input_tokens
    gen_ai.usage.output_tokens
    gen_ai.usage.cache_read.input_tokens
    gen_ai.usage.cache_creation.input_tokens   # alias: cache_write.input_tokens
    gen_ai.usage.reasoning.output_tokens
    gen_ai.conversation.id
    github.copilot.git_repository              # optional; may be absent
    timestamp: OTEL span start (UTC)           # [seconds, nanos] or unix nano

``input_tokens`` includes cache-read; uncached = input − cache read when
input >= cache read (ccusage). Exported Copilot cost fields are ignored.

Do not enable ``COPILOT_OTEL_CAPTURE_CONTENT``. Prompt / completion /
credential attributes are never copied. ``auth.json`` is never opened.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tok.models import UsageEvent
from tok.parsers._common import (
    as_int,
    file_mtime,
    is_secret_path,
    iter_files,
    iter_json_objects,
    make_event,
    parse_timestamp,
    resolve_tool_roots,
)

TOOL = "copilot"

_MODEL_ATTRS = ("gen_ai.response.model", "gen_ai.request.model")
_SESSION_ATTRS = (
    "gen_ai.conversation.id",
    "copilot_chat.session_id",
    "copilot_chat.chat_session_id",
    "session.id",
)
_CACHE_WRITE_ATTRS = (
    "gen_ai.usage.cache_creation.input_tokens",
    "gen_ai.usage.cache_write.input_tokens",
)
_REASONING_ATTRS = (
    "gen_ai.usage.reasoning.output_tokens",
    "gen_ai.usage.reasoning_tokens",
)
_CONTENT_ATTR_MARKERS = (
    "prompt",
    "completion",
    "content",
    "message",
    "messages",
    "body",
    "input.messages",
    "output.messages",
    "authorization",
    "api_key",
    "access_token",
    "refresh_token",
    "cookie",
    "secret",
    "password",
    "credential",
)

_CHAT = "chat"
_INFERENCE = "inference"
_AGENT_TURN = "agent_turn"
_AGENT_SUMMARY = "invoke_agent"


def parse(root: Path | None = None) -> list[UsageEvent]:
    """Parse Copilot OTEL JSONL. Returns ``[]`` if file export is absent."""
    roots = resolve_tool_roots(
        root,
        ".copilot",
        ".config/github-copilot",
        env_vars=("COPILOT_OTEL_FILE_EXPORTER_PATH",),
        undotted_name="copilot",
    )
    events: list[UsageEvent] = []
    seen: set[Path] = set()
    for path in _iter_otel_files(roots):
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)
        try:
            events.extend(_parse_file(path))
        except OSError:
            continue
    return events


def _iter_otel_files(roots: list[Path]):
    for root in roots:
        try:
            if root.is_file():
                if not is_secret_path(root):
                    yield root
                continue
            if not root.is_dir():
                continue
        except OSError:
            continue
        otel = root / "otel"
        scan: list[Path] = []
        try:
            if otel.is_dir():
                scan.append(otel)
            elif root.name == "otel":
                scan.append(root)
            else:
                # Explicit data dir (tests / sample_data/copilot): jsonl only.
                scan.append(root)
        except OSError:
            continue
        for path in iter_files(scan, suffixes=(".jsonl", ".log")):
            if "session-state" in path.parts:
                continue
            yield path


def _parse_file(path: Path) -> list[UsageEvent]:
    records: list[dict[str, Any]] = []
    for obj in iter_json_objects(path):
        records.extend(_iter_span_records(obj))
    if not records:
        return []

    trace_ctx = _collect_trace_contexts(records)
    fallback_ts = file_mtime(path)
    candidates: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        cand = _to_candidate(path, record, index, fallback_ts, trace_ctx)
        if cand is not None:
            candidates.append(cand)
    if not candidates:
        return []

    chat_traces, chat_responses = _source_keys(candidates, _CHAT)
    inf_traces, inf_responses = _source_keys(candidates, _INFERENCE)
    turn_traces, turn_responses = _source_keys(candidates, _AGENT_TURN)

    events: list[UsageEvent] = []
    for cand in candidates:
        if not _should_emit(
            cand,
            chat_traces,
            chat_responses,
            inf_traces,
            inf_responses,
            turn_traces,
            turn_responses,
        ):
            continue
        ev = make_event(
            TOOL,
            path,
            {"model": cand["model"]},
            timestamp=cand["timestamp"],
            model=cand["model"],
            project=cand["project"],
            session_id=cand["session_id"],
            extra={"format": "copilot-otel"},
            tokens=cand["tokens"],
            raw_cost_usd=None,
        )
        if ev is not None:
            events.append(ev)
    return events


def _iter_span_records(obj: Any) -> list[dict[str, Any]]:
    if not isinstance(obj, dict):
        return []
    if isinstance(obj.get("resourceSpans"), list):
        out: list[dict[str, Any]] = []
        for rs in obj["resourceSpans"]:
            if not isinstance(rs, dict):
                continue
            resource = rs.get("resource")
            resource_attrs: dict[str, Any] = {}
            if isinstance(resource, dict):
                resource_attrs = _flatten_attributes(resource.get("attributes"))
            for ss in rs.get("scopeSpans") or []:
                if not isinstance(ss, dict):
                    continue
                for span in ss.get("spans") or []:
                    if not isinstance(span, dict):
                        continue
                    rec = dict(span)
                    rec.setdefault("type", "span")
                    rec["attributes"] = {
                        **resource_attrs,
                        **_flatten_attributes(span.get("attributes")),
                    }
                    out.append(rec)
        return out
    if "attributes" not in obj:
        return []
    rec = dict(obj)
    rec["attributes"] = _flatten_attributes(obj.get("attributes"))
    return [rec]


def _flatten_attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        items = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if not isinstance(key, str) or not key:
                continue
            items.append((key, item.get("value")))
    else:
        return {}
    out: dict[str, Any] = {}
    for key, value in items:
        key_s = str(key)
        if _is_content_attr(key_s):
            continue
        out[key_s] = _otel_value(value)
    return out


def _is_content_attr(key: str) -> bool:
    low = key.lower()
    return any(marker in low for marker in _CONTENT_ATTR_MARKERS)


def _otel_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        return as_int(value["intValue"])
    if "doubleValue" in value:
        return value["doubleValue"]
    if "boolValue" in value:
        return value["boolValue"]
    return value


def _collect_trace_contexts(records: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    contexts: dict[str, dict[str, str]] = {}
    for record in records:
        trace_id = _trace_id(record)
        if not trace_id:
            continue
        attrs = record.get("attributes")
        if not isinstance(attrs, dict):
            continue
        ctx = contexts.setdefault(trace_id, {})
        if "model" not in ctx:
            model = _first_str(attrs, _MODEL_ATTRS)
            if model:
                ctx["model"] = model
        if "session_id" not in ctx:
            session = _first_str(attrs, _SESSION_ATTRS)
            if session:
                ctx["session_id"] = session
        if "project" not in ctx:
            project = _project_from_attrs(attrs)
            if project:
                ctx["project"] = project
    return contexts


def _to_candidate(
    path: Path,
    record: dict[str, Any],
    index: int,
    fallback_ts: datetime,
    trace_ctx: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    attrs = record.get("attributes")
    if not isinstance(attrs, dict):
        return None
    source = _classify(record, attrs)
    if source is None:
        return None

    input_tokens = _attr_int(attrs, "gen_ai.usage.input_tokens")
    output_tokens = _attr_int(attrs, "gen_ai.usage.output_tokens")
    cache_read = _attr_int(attrs, "gen_ai.usage.cache_read.input_tokens")
    cache_write = _attr_int_first(attrs, _CACHE_WRITE_ATTRS)
    reasoning = _attr_int_first(attrs, _REASONING_ATTRS)
    if input_tokens + output_tokens + cache_read + cache_write + reasoning == 0:
        return None

    uncached = input_tokens - cache_read if input_tokens >= cache_read else input_tokens
    trace_id = _trace_id(record)
    ctx = trace_ctx.get(trace_id or "", {})
    model = _first_str(attrs, _MODEL_ATTRS) or ctx.get("model") or "unknown"
    session_id = (
        _first_str(attrs, _SESSION_ATTRS)
        or ctx.get("session_id")
        or trace_id
        or f"unknown-session-{index}"
    )
    project = _project_from_attrs(attrs) or ctx.get("project")
    return {
        "source": source,
        "trace_id": trace_id,
        "response_id": _attr_str(attrs, "gen_ai.response.id"),
        "model": model,
        "session_id": session_id,
        "project": project,
        "timestamp": _span_timestamp(record, fallback_ts),
        "tokens": {
            "input_tokens": uncached,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "reasoning_tokens": reasoning,
        },
    }


def _classify(record: dict[str, Any], attrs: dict[str, Any]) -> str | None:
    op = _attr_str(attrs, "gen_ai.operation.name")
    name = _str(record.get("name")) or ""
    if op == "execute_tool" or name.startswith("execute_tool "):
        return None
    span = _is_span(record)
    if span and (op == "chat" or name.startswith("chat ")):
        return _CHAT
    if span and (op == "invoke_agent" or name.startswith("invoke_agent ")):
        return _AGENT_SUMMARY
    if not span:
        event_name = _attr_str(attrs, "event.name")
        body = _str(record.get("body")) or _str(record.get("_body")) or ""
        if event_name == "gen_ai.client.inference.operation.details" or body.startswith(
            "GenAI inference:"
        ):
            return _INFERENCE
        if event_name == "copilot_chat.agent.turn" or body.startswith("copilot_chat.agent.turn"):
            return _AGENT_TURN
    return None


def _is_span(record: dict[str, Any]) -> bool:
    rec_type = record.get("type")
    if isinstance(rec_type, str):
        return rec_type == "span"
    name = _str(record.get("name"))
    return bool(
        name
        and (
            _str(record.get("spanId"))
            or _str(record.get("traceId"))
            or record.get("startTime") is not None
            or record.get("endTime") is not None
            or record.get("startTimeUnixNano") is not None
            or record.get("kind") is not None
        )
    )


def _source_keys(
    candidates: list[dict[str, Any]], source: str
) -> tuple[set[str], set[str]]:
    traces: set[str] = set()
    responses: set[str] = set()
    for cand in candidates:
        if cand["source"] != source:
            continue
        if cand.get("trace_id"):
            traces.add(cand["trace_id"])
        if cand.get("response_id"):
            responses.add(cand["response_id"])
    return traces, responses


def _should_emit(
    cand: dict[str, Any],
    chat_traces: set[str],
    chat_responses: set[str],
    inf_traces: set[str],
    inf_responses: set[str],
    turn_traces: set[str],
    turn_responses: set[str],
) -> bool:
    source = cand["source"]
    if source == _CHAT:
        return True
    if source == _INFERENCE:
        return not _hit(cand, chat_traces, chat_responses)
    if source == _AGENT_TURN:
        return not _hit(cand, chat_traces, chat_responses) and not _hit(
            cand, inf_traces, inf_responses
        )
    if source == _AGENT_SUMMARY:
        return (
            not _hit(cand, chat_traces, chat_responses)
            and not _hit(cand, inf_traces, inf_responses)
            and not _hit(cand, turn_traces, turn_responses)
        )
    return False


def _hit(cand: dict[str, Any], traces: set[str], responses: set[str]) -> bool:
    trace_id = cand.get("trace_id")
    response_id = cand.get("response_id")
    if trace_id and trace_id in traces:
        return True
    if response_id and response_id in responses:
        return True
    return False


def _project_from_attrs(attrs: dict[str, Any]) -> str | None:
    raw = _attr_str(attrs, "github.copilot.git_repository")
    if not raw:
        return None
    raw = raw.rstrip("/")
    if raw.endswith(".git"):
        raw = raw[: -len(".git")]
    label = Path(raw).name
    return label or raw


def _span_timestamp(record: dict[str, Any], fallback: datetime) -> datetime:
    for key in (
        "startTime",
        "startTimeUnixNano",
        "start_time",
        "endTime",
        "endTimeUnixNano",
        "hrTime",
        "_hrTime",
        "time",
        "timestamp",
        "observedTimestamp",
        "timeUnixNano",
    ):
        if key not in record:
            continue
        ts = _coerce_otel_time(record[key])
        if ts is not None:
            return ts
    return fallback if fallback.tzinfo else fallback.replace(tzinfo=timezone.utc)


def _coerce_otel_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, list) and value:
        try:
            seconds = float(value[0])
        except (TypeError, ValueError):
            seconds = float(as_int(value[0]))
        nanos = 0.0
        if len(value) > 1:
            try:
                nanos = float(value[1])
            except (TypeError, ValueError):
                nanos = float(as_int(value[1]))
        if seconds <= 0:
            return None
        try:
            return datetime.fromtimestamp(seconds + nanos / 1e9, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.isdigit():
            return _coerce_otel_time(int(s))
        ts = parse_timestamp(s)
        return ts
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        n = float(value)
        if n <= 0:
            return None
        if n >= 1e16:  # unix nanos
            n /= 1e9
        elif n >= 1e14:  # unix micros
            n /= 1e6
        elif n >= 1e11:  # unix millis
            n /= 1e3
        try:
            return datetime.fromtimestamp(n, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    return None


def _trace_id(record: dict[str, Any]) -> str | None:
    found = _str(record.get("traceId"))
    if found:
        return found
    ctx = record.get("spanContext")
    if isinstance(ctx, dict):
        return _str(ctx.get("traceId"))
    return None


def _first_str(attrs: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        found = _attr_str(attrs, key)
        if found:
            return found
    return None


def _attr_str(attrs: dict[str, Any], key: str) -> str | None:
    return _str(attrs.get(key))


def _attr_int(attrs: dict[str, Any], key: str) -> int:
    return _as_nonneg_int(attrs.get(key))


def _attr_int_first(attrs: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        n = _attr_int(attrs, key)
        if n:
            return n
    return 0


def _as_nonneg_int(value: Any) -> int:
    if isinstance(value, dict):
        value = _otel_value(value)
    return as_int(value)


def _str(value: Any) -> str | None:
    if isinstance(value, dict):
        value = _otel_value(value)
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return None
