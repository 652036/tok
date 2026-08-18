"""Filter and group UsageEvent lists for CLI summaries."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from tok.models import CostBreakdown, UsageEvent
from tok.pricing import price_event

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass
class GroupStats:
    """Token sums, cost sums, and event count for one group key."""

    key: str
    event_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost: CostBreakdown = field(default_factory=CostBreakdown)

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.reasoning_tokens
        )

    def add_event(self, event: UsageEvent, cost: CostBreakdown | None = None) -> None:
        self.event_count += 1
        self.input_tokens += event.input_tokens
        self.output_tokens += event.output_tokens
        self.cache_read_tokens += event.cache_read_tokens
        self.cache_write_tokens += event.cache_write_tokens
        self.reasoning_tokens += event.reasoning_tokens
        breakdown = cost if cost is not None else price_event(event)
        if self.event_count == 1:
            self.cost = breakdown
        else:
            self.cost += breakdown


def _as_aware(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=ZoneInfo("UTC"))
    return ts


def _bound(value: datetime | date | None, *, end: bool) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_aware(value)
    # date: interpret as a calendar day in Asia/Shanghai
    if end:
        return datetime.combine(value, time.max, tzinfo=SHANGHAI)
    return datetime.combine(value, time.min, tzinfo=SHANGHAI)


def filter_events(
    events: Iterable[UsageEvent],
    since: datetime | date | None = None,
    until: datetime | date | None = None,
    tool: str | None = None,
) -> list[UsageEvent]:
    """Return events matching an inclusive time window and optional tool name.

    ``since`` / ``until`` as ``date`` are that calendar day in Asia/Shanghai.
    Tool comparison is case-insensitive.
    """
    start = _bound(since, end=False)
    stop = _bound(until, end=True)
    tool_key = tool.lower() if tool else None
    out: list[UsageEvent] = []
    for event in events:
        if tool_key is not None and event.tool.lower() != tool_key:
            continue
        ts = _as_aware(event.timestamp)
        if start is not None and ts < start:
            continue
        if stop is not None and ts > stop:
            continue
        out.append(event)
    return out


def _group(
    events: Iterable[UsageEvent],
    key_fn: Callable[[UsageEvent], str],
) -> dict[str, GroupStats]:
    groups: dict[str, GroupStats] = {}
    for event in events:
        key = key_fn(event)
        stats = groups.get(key)
        if stats is None:
            stats = GroupStats(key=key)
            groups[key] = stats
        stats.add_event(event)
    return groups


def group_by_tool(events: Iterable[UsageEvent]) -> dict[str, GroupStats]:
    return _group(events, lambda e: e.tool)


def group_by_model(events: Iterable[UsageEvent]) -> dict[str, GroupStats]:
    return _group(events, lambda e: e.model)


def group_by_day(events: Iterable[UsageEvent]) -> dict[str, GroupStats]:
    """Bucket by local Asia/Shanghai calendar date (YYYY-MM-DD)."""

    def key_fn(event: UsageEvent) -> str:
        return _as_aware(event.timestamp).astimezone(SHANGHAI).date().isoformat()

    return _group(events, key_fn)


def group_by_project(events: Iterable[UsageEvent]) -> dict[str, GroupStats]:
    return _group(events, lambda e: e.project or "")


def group_by_session(events: Iterable[UsageEvent]) -> dict[str, GroupStats]:
    return _group(events, lambda e: e.session_id or "")
