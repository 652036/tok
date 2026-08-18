"""Render usage tables.

Uses Rich when installed; otherwise falls back to aligned plain text.
Report functions accept in-memory events (duck-typed UsageEvent) so they
can be unit-tested without parsers or local log files.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TextIO
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

try:
    from rich import box
    from rich.console import Console
    from rich.table import Table

    HAS_RICH = True
except ImportError:  # pragma: no cover - optional extra
    box = None  # type: ignore[assignment]
    Console = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    HAS_RICH = False


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_tokens(n: int | float | None) -> str:
    """Format a token count with thousands separators."""
    try:
        value = int(n or 0)
    except (TypeError, ValueError):
        value = 0
    return f"{value:,}"


def format_money(amount: float | None) -> str:
    """Format USD as ``$1,234.5678``.

    Uses 4 decimal places by default and 5–6 places for small amounts so
    sub-cent costs do not collapse to ``$0.0000``.
    """
    try:
        value = float(amount or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    if value == 0.0:
        return "$0.0000"
    sign = "-" if value < 0 else ""
    amt = abs(value)
    if amt < 0.01:
        return f"{sign}${amt:,.6f}"
    if amt < 1.0:
        return f"{sign}${amt:,.5f}"
    return f"{sign}${amt:,.4f}"


def event_day(event: Any, tz: Any = SHANGHAI) -> str:
    """Calendar day of an event in ``tz`` (default Asia/Shanghai)."""
    ts = _as_aware(getattr(event, "timestamp", None))
    return ts.astimezone(tz).date().isoformat()


def session_key(event: Any) -> str:
    """Stable session identity; falls back to source+timestamp."""
    sid = getattr(event, "session_id", None)
    if sid:
        return str(sid)
    source = getattr(event, "source_path", "") or ""
    ts = getattr(event, "timestamp", None)
    ts_s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts or "")
    return f"{source}:{ts_s}"


def total_tokens_of(event: Any) -> int:
    return (
        _int(getattr(event, "input_tokens", 0))
        + _int(getattr(event, "output_tokens", 0))
        + _int(getattr(event, "cache_read_tokens", 0))
        + _int(getattr(event, "cache_write_tokens", 0))
        + _int(getattr(event, "reasoning_tokens", 0))
    )


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

@dataclass
class ReportRow:
    """One aggregated line in a usage table."""

    tool: str = ""
    model: str = ""
    project: str = ""
    day: str = ""
    session_id: str = ""
    days: int = 0
    sessions: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    unpriced_tokens: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "model": self.model,
            "project": self.project,
            "day": self.day,
            "session_id": self.session_id,
            "days": self.days,
            "sessions": self.sessions,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "api_usd": self.cost_usd,
            "unpriced_tokens": self.unpriced_tokens,
            **self.extra,
        }


def totals_row(rows: Sequence[ReportRow]) -> ReportRow:
    day_union: set[str] = set()
    session_union = 0
    for row in rows:
        if row.day:
            day_union.add(row.day)
        session_union += int(row.sessions or 0)
    # days: unique calendar days if every row has a day; else sum is misleading
    # so prefer max-of-days when grouping is not by-day
    days_val = len(day_union) if day_union else sum(int(r.days or 0) for r in rows)
    return ReportRow(
        tool="TOTAL",
        model="",
        project="",
        day="",
        session_id="",
        days=days_val,
        sessions=session_union,
        input_tokens=sum(r.input_tokens for r in rows),
        output_tokens=sum(r.output_tokens for r in rows),
        cache_read_tokens=sum(r.cache_read_tokens for r in rows),
        cache_write_tokens=sum(r.cache_write_tokens for r in rows),
        reasoning_tokens=sum(r.reasoning_tokens for r in rows),
        total_tokens=sum(r.total_tokens for r in rows),
        cost_usd=sum(r.cost_usd for r in rows),
        unpriced_tokens=sum(r.unpriced_tokens for r in rows),
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def price_events(events: Sequence[Any], costs: Sequence[Any] | None = None) -> list[Any]:
    """Return a cost object per event.

    Uses the provided ``costs`` when aligned; otherwise calls
    ``pricing.price_event`` when importable; otherwise uses ``raw_cost_usd``.
    """
    out: list[Any] = []
    n = len(events)
    provided = list(costs) if costs is not None else []
    price_fn = None
    try:
        from tok.pricing import price_event as price_fn  # type: ignore
    except ImportError:
        price_fn = None

    for i, event in enumerate(events):
        if i < len(provided) and provided[i] is not None:
            out.append(provided[i])
            continue
        if price_fn is not None:
            try:
                out.append(price_fn(event))
                continue
            except Exception:
                pass
        raw = getattr(event, "raw_cost_usd", None)
        out.append(
            _SimpleCost(
                total_usd=float(raw) if raw is not None else 0.0,
                priced=raw is not None,
                source="log" if raw is not None else "unknown",
            )
        )
    if len(out) < n:  # pragma: no cover
        out.extend([_SimpleCost() for _ in range(n - len(out))])
    return out


@dataclass
class _SimpleCost:
    input_usd: float = 0.0
    output_usd: float = 0.0
    cache_read_usd: float = 0.0
    cache_write_usd: float = 0.0
    reasoning_usd: float = 0.0
    total_usd: float = 0.0
    priced: bool = False
    source: str = "unknown"


@dataclass
class _Bucket:
    tool: str = ""
    model: str = ""
    project: str = ""
    day: str = ""
    session_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    unpriced_tokens: int = 0
    day_set: set[str] = field(default_factory=set)
    session_set: set[str] = field(default_factory=set)
    tools: set[str] = field(default_factory=set)
    models: set[str] = field(default_factory=set)

    def add(self, event: Any, cost: Any) -> None:
        inp = _int(getattr(event, "input_tokens", 0))
        out = _int(getattr(event, "output_tokens", 0))
        cr = _int(getattr(event, "cache_read_tokens", 0))
        cw = _int(getattr(event, "cache_write_tokens", 0))
        rs = _int(getattr(event, "reasoning_tokens", 0))
        self.input_tokens += inp
        self.output_tokens += out
        self.cache_read_tokens += cr
        self.cache_write_tokens += cw
        self.reasoning_tokens += rs
        tok = inp + out + cr + cw + rs
        priced = bool(getattr(cost, "priced", False))
        usd = float(getattr(cost, "total_usd", 0.0) or 0.0)
        if priced:
            self.cost_usd += usd
        else:
            # Still add a log-provided amount if present, but mark tokens unpriced
            # only when the pricer said the model is unknown (no usable USD).
            if usd:
                self.cost_usd += usd
            else:
                self.unpriced_tokens += tok
        day = event_day(event)
        self.day_set.add(day)
        self.session_set.add(session_key(event))
        tool = str(getattr(event, "tool", "") or "")
        model = str(getattr(event, "model", "") or "")
        if tool:
            self.tools.add(tool)
        if model:
            self.models.add(model)

    def to_row(self) -> ReportRow:
        tool = self.tool or (_join(self.tools) if self.tools else "")
        model = self.model or (_join(self.models) if len(self.models) == 1 else "")
        return ReportRow(
            tool=tool,
            model=model,
            project=self.project,
            day=self.day,
            session_id=self.session_id,
            days=len(self.day_set),
            sessions=len(self.session_set),
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
            reasoning_tokens=self.reasoning_tokens,
            total_tokens=(
                self.input_tokens
                + self.output_tokens
                + self.cache_read_tokens
                + self.cache_write_tokens
                + self.reasoning_tokens
            ),
            cost_usd=self.cost_usd,
            unpriced_tokens=self.unpriced_tokens,
        )


def aggregate_rows(
    events: Sequence[Any],
    costs: Sequence[Any] | None = None,
    *,
    group_by: str = "tool",
) -> list[ReportRow]:
    """Group priced events into :class:`ReportRow` values.

    ``group_by`` is one of ``tool``, ``model``, ``day``, ``project``, ``session``.
    """
    if not events:
        return []
    priced = price_events(events, costs)
    buckets: dict[tuple[str, ...], _Bucket] = {}

    for event, cost in zip(events, priced, strict=False):
        key, seed = _group_key(event, group_by)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _Bucket(**seed)
            buckets[key] = bucket
        bucket.add(event, cost)

    rows = [b.to_row() for b in buckets.values()]
    return _sort_rows(rows, group_by)


def _group_key(event: Any, group_by: str) -> tuple[tuple[str, ...], dict[str, str]]:
    tool = str(getattr(event, "tool", "") or "")
    model = str(getattr(event, "model", "") or "")
    project = getattr(event, "project", None)
    project_s = str(project) if project else "(none)"
    day = event_day(event)
    sess = session_key(event)
    if group_by in ("tool", "summary"):
        return (tool,), {"tool": tool}
    if group_by in ("model", "by-model"):
        return (tool, model), {"tool": tool, "model": model}
    if group_by in ("day", "by-day"):
        return (day,), {"day": day}
    if group_by in ("project", "by-project"):
        return (project_s,), {"project": project_s}
    if group_by in ("session", "sessions"):
        return (sess,), {"session_id": sess, "tool": tool, "model": model, "project": project_s}
    return (tool,), {"tool": tool}


def _sort_rows(rows: list[ReportRow], group_by: str) -> list[ReportRow]:
    if group_by in ("day", "by-day"):
        return sorted(rows, key=lambda r: (r.day, -r.cost_usd, -r.total_tokens))
    if group_by in ("session", "sessions"):
        return sorted(rows, key=lambda r: (-r.cost_usd, -r.total_tokens, r.session_id))
    return sorted(rows, key=lambda r: (-r.cost_usd, -r.total_tokens, r.tool, r.model, r.project))


# ---------------------------------------------------------------------------
# Public print_* API
# ---------------------------------------------------------------------------

_SUMMARY_COLS = (
    ("tool", "Tool", "left"),
    ("days", "Days", "right"),
    ("sessions", "Sessions", "right"),
    ("input_tokens", "Input", "right"),
    ("output_tokens", "Output", "right"),
    ("cache_read_tokens", "Cache Read", "right"),
    ("cache_write_tokens", "Cache Write", "right"),
    ("reasoning_tokens", "Reasoning", "right"),
    ("total_tokens", "Total Tokens", "right"),
    ("cost_usd", "API USD", "right"),
)

_MODEL_COLS = (
    ("tool", "Tool", "left"),
    ("model", "Model", "left"),
    ("days", "Days", "right"),
    ("sessions", "Sessions", "right"),
    ("input_tokens", "Input", "right"),
    ("output_tokens", "Output", "right"),
    ("cache_read_tokens", "Cache Read", "right"),
    ("cache_write_tokens", "Cache Write", "right"),
    ("reasoning_tokens", "Reasoning", "right"),
    ("total_tokens", "Total Tokens", "right"),
    ("cost_usd", "API USD", "right"),
)

_DAY_COLS = (
    ("day", "Day", "left"),
    ("tool", "Tool", "left"),
    ("sessions", "Sessions", "right"),
    ("input_tokens", "Input", "right"),
    ("output_tokens", "Output", "right"),
    ("cache_read_tokens", "Cache Read", "right"),
    ("cache_write_tokens", "Cache Write", "right"),
    ("reasoning_tokens", "Reasoning", "right"),
    ("total_tokens", "Total Tokens", "right"),
    ("cost_usd", "API USD", "right"),
)

_PROJECT_COLS = (
    ("project", "Project", "left"),
    ("tool", "Tool", "left"),
    ("days", "Days", "right"),
    ("sessions", "Sessions", "right"),
    ("input_tokens", "Input", "right"),
    ("output_tokens", "Output", "right"),
    ("cache_read_tokens", "Cache Read", "right"),
    ("cache_write_tokens", "Cache Write", "right"),
    ("reasoning_tokens", "Reasoning", "right"),
    ("total_tokens", "Total Tokens", "right"),
    ("cost_usd", "API USD", "right"),
)

_SESSION_COLS = (
    ("session_id", "Session", "left"),
    ("tool", "Tool", "left"),
    ("model", "Model", "left"),
    ("project", "Project", "left"),
    ("days", "Days", "right"),
    ("input_tokens", "Input", "right"),
    ("output_tokens", "Output", "right"),
    ("cache_read_tokens", "Cache Read", "right"),
    ("cache_write_tokens", "Cache Write", "right"),
    ("reasoning_tokens", "Reasoning", "right"),
    ("total_tokens", "Total Tokens", "right"),
    ("cost_usd", "API USD", "right"),
)

_DISCOVER_COLS = (
    ("tool", "Tool", "left"),
    ("path", "Path", "left"),
    ("found", "Found", "left"),
    ("notes", "Notes", "left"),
)


def print_summary(
    events: Sequence[Any] | None = None,
    costs: Sequence[Any] | None = None,
    *,
    rows: Sequence[Any] | None = None,
    json_output: bool = False,
    file: TextIO | None = None,
    title: str = "Usage by tool",
) -> None:
    """Print totals grouped by tool."""
    _print_grouped(
        events,
        costs,
        rows=rows,
        group_by="tool",
        columns=_SUMMARY_COLS,
        json_output=json_output,
        file=file,
        title=title,
    )


def print_by_model(
    events: Sequence[Any] | None = None,
    costs: Sequence[Any] | None = None,
    *,
    rows: Sequence[Any] | None = None,
    json_output: bool = False,
    file: TextIO | None = None,
    title: str = "Usage by model",
) -> None:
    _print_grouped(
        events,
        costs,
        rows=rows,
        group_by="model",
        columns=_MODEL_COLS,
        json_output=json_output,
        file=file,
        title=title,
    )


def print_by_day(
    events: Sequence[Any] | None = None,
    costs: Sequence[Any] | None = None,
    *,
    rows: Sequence[Any] | None = None,
    json_output: bool = False,
    file: TextIO | None = None,
    title: str = "Usage by day (Asia/Shanghai)",
) -> None:
    _print_grouped(
        events,
        costs,
        rows=rows,
        group_by="day",
        columns=_DAY_COLS,
        json_output=json_output,
        file=file,
        title=title,
    )


def print_by_project(
    events: Sequence[Any] | None = None,
    costs: Sequence[Any] | None = None,
    *,
    rows: Sequence[Any] | None = None,
    json_output: bool = False,
    file: TextIO | None = None,
    title: str = "Usage by project",
) -> None:
    _print_grouped(
        events,
        costs,
        rows=rows,
        group_by="project",
        columns=_PROJECT_COLS,
        json_output=json_output,
        file=file,
        title=title,
    )


def print_sessions(
    events: Sequence[Any] | None = None,
    costs: Sequence[Any] | None = None,
    *,
    rows: Sequence[Any] | None = None,
    json_output: bool = False,
    file: TextIO | None = None,
    title: str = "Usage by session",
) -> None:
    _print_grouped(
        events,
        costs,
        rows=rows,
        group_by="session",
        columns=_SESSION_COLS,
        json_output=json_output,
        file=file,
        title=title,
    )


def print_discover(
    entries: Sequence[Any] | None = None,
    *,
    json_output: bool = False,
    file: TextIO | None = None,
    title: str = "Local data directories",
) -> None:
    """Print discovered CLI data directories.

    Each entry is a mapping or object with ``tool``, ``path``, ``found``,
    and optional ``notes``.
    """
    out = file or sys.stdout
    rows = [_normalize_discover(e) for e in (entries or [])]
    if json_output:
        json.dump({"command": "discover", "entries": rows}, out, indent=2, ensure_ascii=False)
        out.write("\n")
        return
    if not rows:
        out.write("No known CLI data directories were found.\n")
        out.write("Hint: pass --home if your files live under a different home directory.\n")
        return
    headers = [c[1] for c in _DISCOVER_COLS]
    keys = [c[0] for c in _DISCOVER_COLS]
    aligns = [c[2] for c in _DISCOVER_COLS]
    cells = [[_discover_cell(r, k) for k in keys] for r in rows]
    _render_table(headers, cells, aligns, title=title, file=out)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _print_grouped(
    events: Sequence[Any] | None,
    costs: Sequence[Any] | None,
    *,
    rows: Sequence[Any] | None,
    group_by: str,
    columns: Sequence[tuple[str, str, str]],
    json_output: bool,
    file: TextIO | None,
    title: str,
) -> None:
    out = file or sys.stdout
    report_rows = _coerce_rows(rows, events, costs, group_by)
    totals = totals_row(report_rows) if report_rows else ReportRow(tool="TOTAL")
    unpriced = totals.unpriced_tokens

    if json_output:
        payload = {
            "command": group_by,
            "rows": [r.to_dict() for r in report_rows],
            "totals": totals.to_dict(),
            "unpriced_tokens": unpriced,
        }
        json.dump(payload, out, indent=2, ensure_ascii=False)
        out.write("\n")
        return

    if not report_rows:
        out.write("No rows to display.\n")
        return

    headers = [c[1] for c in columns]
    keys = [c[0] for c in columns]
    aligns = [c[2] for c in columns]
    cells = [[_format_cell(r, k) for k in keys] for r in report_rows]
    total_cells = [_format_cell(totals, k, is_total=True) for k in keys]
    _render_table(headers, cells, aligns, title=title, totals=total_cells, file=out)
    if unpriced:
        out.write(
            f"Note: {format_tokens(unpriced)} tokens from unknown/unpriced models "
            "are counted in tokens but contribute $0 to API USD.\n"
        )


def _coerce_rows(
    rows: Sequence[Any] | None,
    events: Sequence[Any] | None,
    costs: Sequence[Any] | None,
    group_by: str,
) -> list[ReportRow]:
    if rows is not None:
        return [_as_report_row(r) for r in rows]
    return aggregate_rows(list(events or []), costs, group_by=group_by)


def _as_report_row(row: Any) -> ReportRow:
    if isinstance(row, ReportRow):
        return row
    if isinstance(row, Mapping):
        known = {k: row[k] for k in ReportRow.__dataclass_fields__ if k in row}  # type: ignore[attr-defined]
        if "api_usd" in row and "cost_usd" not in known:
            known["cost_usd"] = float(row.get("api_usd") or 0.0)
        if "total_tokens" not in known:
            known["total_tokens"] = (
                _int(known.get("input_tokens"))
                + _int(known.get("output_tokens"))
                + _int(known.get("cache_read_tokens"))
                + _int(known.get("cache_write_tokens"))
                + _int(known.get("reasoning_tokens"))
            )
        extra = {k: v for k, v in row.items() if k not in ReportRow.__dataclass_fields__ and k != "api_usd"}
        return ReportRow(**known, extra=extra)  # type: ignore[arg-type]
    return ReportRow(
        tool=str(getattr(row, "tool", "") or ""),
        model=str(getattr(row, "model", "") or ""),
        project=str(getattr(row, "project", "") or ""),
        day=str(getattr(row, "day", "") or ""),
        session_id=str(getattr(row, "session_id", "") or ""),
        days=_int(getattr(row, "days", 0)),
        sessions=_int(getattr(row, "sessions", 0)),
        input_tokens=_int(getattr(row, "input_tokens", 0)),
        output_tokens=_int(getattr(row, "output_tokens", 0)),
        cache_read_tokens=_int(getattr(row, "cache_read_tokens", 0)),
        cache_write_tokens=_int(getattr(row, "cache_write_tokens", 0)),
        reasoning_tokens=_int(getattr(row, "reasoning_tokens", 0)),
        total_tokens=_int(getattr(row, "total_tokens", 0)) or total_tokens_of(row),
        cost_usd=float(getattr(row, "cost_usd", getattr(row, "api_usd", 0.0)) or 0.0),
        unpriced_tokens=_int(getattr(row, "unpriced_tokens", 0)),
    )


def _format_cell(row: ReportRow, key: str, *, is_total: bool = False) -> str:
    if key == "tool":
        return "TOTAL" if is_total else (row.tool or "")
    if key == "model":
        return "" if is_total else (row.model or "")
    if key == "project":
        return "" if is_total else (row.project or "")
    if key == "day":
        return "" if is_total else (row.day or "")
    if key == "session_id":
        return "TOTAL" if is_total else _short_session(row.session_id)
    if key == "days":
        return format_tokens(row.days)
    if key == "sessions":
        return format_tokens(row.sessions)
    if key == "input_tokens":
        return format_tokens(row.input_tokens)
    if key == "output_tokens":
        return format_tokens(row.output_tokens)
    if key == "cache_read_tokens":
        return format_tokens(row.cache_read_tokens)
    if key == "cache_write_tokens":
        return format_tokens(row.cache_write_tokens)
    if key == "reasoning_tokens":
        return format_tokens(row.reasoning_tokens)
    if key == "total_tokens":
        return format_tokens(row.total_tokens)
    if key == "cost_usd":
        return format_money(row.cost_usd)
    return str(getattr(row, key, "") or "")


def _short_session(session_id: str, width: int = 28) -> str:
    if not session_id:
        return ""
    if len(session_id) <= width:
        return session_id
    return session_id[: width - 3] + "..."


def _normalize_discover(entry: Any) -> dict[str, Any]:
    if isinstance(entry, Mapping):
        return {
            "tool": str(entry.get("tool", "") or ""),
            "path": str(entry.get("path", entry.get("root", "")) or ""),
            "found": bool(entry.get("found", entry.get("exists", False))),
            "notes": str(entry.get("notes", entry.get("detail", "")) or ""),
        }
    return {
        "tool": str(getattr(entry, "tool", "") or ""),
        "path": str(getattr(entry, "path", getattr(entry, "root", "")) or ""),
        "found": bool(getattr(entry, "found", getattr(entry, "exists", False))),
        "notes": str(getattr(entry, "notes", getattr(entry, "detail", "")) or ""),
    }


def _discover_cell(row: Mapping[str, Any], key: str) -> str:
    if key == "found":
        return "yes" if row.get("found") else "no"
    return str(row.get(key, "") or "")


def _render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    aligns: Sequence[str],
    *,
    title: str,
    totals: Sequence[str] | None = None,
    file: TextIO,
) -> None:
    if HAS_RICH:
        _render_rich(headers, rows, aligns, title=title, totals=totals, file=file)
        return
    _render_plain(headers, rows, aligns, title=title, totals=totals, file=file)


def _console_width(file: TextIO) -> int:
    """Wide enough that captured/piped tables are not ellipsized."""
    is_tty = bool(getattr(file, "isatty", lambda: False)())
    if is_tty:
        columns = shutil.get_terminal_size(fallback=(120, 24)).columns
        return max(columns, 120)
    return 160


_IDENTITY_HEADERS = frozenset({"Tool", "Day", "Project"})


def _render_rich(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    aligns: Sequence[str],
    *,
    title: str,
    totals: Sequence[str] | None,
    file: TextIO,
) -> None:
    # Rich 15 treats TERM=dumb as 80x25 unless BOTH width and height are set.
    console = Console(
        file=file,
        highlight=False,
        soft_wrap=False,
        width=_console_width(file),
        height=max(shutil.get_terminal_size(fallback=(120, 24)).lines, 24),
    )
    table = Table(
        title=title,
        box=box.SIMPLE_HEAVY,
        show_footer=bool(totals),
        header_style="bold",
        expand=False,
    )
    for header, align, footer in zip(
        headers,
        aligns,
        totals or [""] * len(headers),
        strict=False,
    ):
        justify = "right" if align == "right" else "left"
        table.add_column(
            header,
            justify=justify,
            footer=footer or "",
            no_wrap=header in _IDENTITY_HEADERS,
        )
    for row in rows:
        table.add_row(*[str(c) for c in row])
    console.print(table)


def _render_plain(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    aligns: Sequence[str],
    *,
    title: str,
    totals: Sequence[str] | None,
    file: TextIO,
) -> None:
    all_rows: list[Sequence[str]] = [headers, *rows]
    if totals:
        all_rows.append(totals)
    widths = [0] * len(headers)
    for row in all_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    file.write(f"{title}\n")
    file.write(_plain_line(headers, widths, aligns, header=True) + "\n")
    file.write(_plain_rule(widths) + "\n")
    for row in rows:
        file.write(_plain_line(row, widths, aligns) + "\n")
    if totals:
        file.write(_plain_rule(widths) + "\n")
        file.write(_plain_line(totals, widths, aligns) + "\n")


def _plain_line(
    cells: Sequence[str],
    widths: Sequence[int],
    aligns: Sequence[str],
    *,
    header: bool = False,
) -> str:
    parts: list[str] = []
    for cell, width, align in zip(cells, widths, aligns, strict=False):
        text = str(cell)
        if header or align != "right":
            parts.append(text.ljust(width))
        else:
            parts.append(text.rjust(width))
    return "  ".join(parts).rstrip()


def _plain_rule(widths: Sequence[int]) -> str:
    return "  ".join("-" * w for w in widths)


def _as_aware(ts: Any) -> datetime:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _join(values: Iterable[str]) -> str:
    return ",".join(sorted(v for v in values if v))
