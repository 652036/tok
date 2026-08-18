"""User-facing CLI for local AI-CLI usage stats.

Binary: tok (deprecated alias: ai-usage).

Commands: (none=summary), m/model/by-model, d/day/by-day,
p/proj/project/by-project, s/sess/session/sessions, w/where/discover.
Dependency modules (parsers, pricing, aggregate, discover, models) are imported
lazily so a partial checkout still produces a clear error instead of a crash.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import sys
import traceback
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO
from zoneinfo import ZoneInfo

from tok import __version__
from tok.report import (
    print_by_day,
    print_by_model,
    print_by_project,
    print_discover,
    print_sessions,
    print_summary,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")

TOOLS = ("claude", "codex", "grok", "gemini", "aider", "opencode", "amp", "copilot")

COMMANDS = ("summary", "m", "d", "p", "s", "w")

# argparse dest is the primary name; aliases and legacy names map here.
_CANONICAL_COMMAND = {
    None: "summary",
    "summary": "summary",
    "m": "by-model",
    "model": "by-model",
    "by-model": "by-model",
    "d": "by-day",
    "day": "by-day",
    "by-day": "by-day",
    "p": "by-project",
    "proj": "by-project",
    "project": "by-project",
    "by-project": "by-project",
    "s": "sessions",
    "sess": "sessions",
    "session": "sessions",
    "sessions": "sessions",
    "w": "discover",
    "where": "discover",
    "discover": "discover",
}

# Typical locations used only when discover.py is not importable.
_FALLBACK_DIRS: dict[str, tuple[str, ...]] = {
    "claude": (".claude", ".config/claude"),
    "codex": (".codex",),
    "grok": (".grok", ".config/grok"),
    "gemini": (".gemini",),
    "aider": (".aider",),
    "opencode": (".local/share/opencode", ".opencode"),
    "amp": (".amp", ".config/amp"),
    "copilot": (".copilot",),
}

_NO_DATA = "No local usage data found"


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def _date_flag(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected YYYY-MM-DD, got {value!r}"
        ) from exc
    return value


def _add_common_flags(
    parser: argparse.ArgumentParser,
    *,
    suppress: bool = False,
) -> None:
    """Add shared flags. ``suppress=True`` for subparsers so omitted flags
    do not overwrite values parsed by the parent (home before *or* after).
    """
    default: Any = argparse.SUPPRESS if suppress else None
    json_default: Any = argparse.SUPPRESS if suppress else False
    parser.add_argument(
        "-S",
        "--since",
        metavar="YYYY-MM-DD",
        type=_date_flag,
        default=default,
        help="Include events on/after this date (Asia/Shanghai midnight)",
    )
    parser.add_argument(
        "-U",
        "--until",
        metavar="YYYY-MM-DD",
        type=_date_flag,
        default=default,
        help="Include events on/before this date (Asia/Shanghai, inclusive)",
    )
    parser.add_argument(
        "-t",
        "--tool",
        metavar="TOOL",
        default=default,
        help=(
            "Filter by tool name (comma-separated). "
            "One of: " + ", ".join(TOOLS)
        ),
    )
    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        dest="json_output",
        default=json_default,
        help="Output JSON instead of a table",
    )
    parser.add_argument(
        "-H",
        "--home",
        type=Path,
        default=default,
        help="Override home directory used to locate CLI data (or set TOK_HOME)",
    )


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_flags(common)

    # Subparser copies of the same flags use SUPPRESS so e.g. ``tok --home X m``
    # is not overwritten by the subparser default when --home is only on parent.
    common_sub = argparse.ArgumentParser(add_help=False)
    _add_common_flags(common_sub, suppress=True)

    parser = argparse.ArgumentParser(
        prog="tok",
        description=(
            "Local token and API-equivalent cost stats for AI coding CLIs. "
            "Reads usage metadata from this machine only; never uploads data."
        ),
        epilog=(
            "No command prints a summary by tool.\n"
            "Dates are interpreted as Asia/Shanghai midnight. "
            "-U/--until is inclusive of that calendar day."
        ),
        parents=[common],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"tok {__version__}",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def _cmd(name: str, aliases: list[str], help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(
            name,
            aliases=aliases,
            parents=[common_sub],
            help=help_text,
            argument_default=argparse.SUPPRESS,
        )

    _cmd("summary", [], "Totals by tool (default; same as no command)")
    _cmd("m", ["model", "by-model"], "Totals by model")
    _cmd("d", ["day", "by-day"], "Totals by day (Asia/Shanghai)")
    _cmd("p", ["proj", "project", "by-project"], "Totals by project")
    _cmd("s", ["sess", "session", "sessions"], "Per-session breakdown")
    _cmd("w", ["where", "discover"], "Show local data directories")
    return parser


# ---------------------------------------------------------------------------
# Date / filter helpers
# ---------------------------------------------------------------------------

def shanghai_midnight(date_str: str) -> datetime:
    """Return timezone-aware UTC instant for YYYY-MM-DD 00:00 Asia/Shanghai."""
    local = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=SHANGHAI)
    return local.astimezone(timezone.utc)


def until_exclusive(date_str: str) -> datetime:
    """First instant *after* the inclusive --until calendar day."""
    return shanghai_midnight(date_str) + timedelta(days=1)


def resolve_home(home: Path | str | None) -> Path:
    if home:
        return Path(home).expanduser()
    from tok.discover import default_home

    return default_home()


def parse_tool_filter(value: str | None) -> set[str] | None:
    if not value:
        return None
    parts = {p.strip().lower() for p in value.split(",") if p.strip()}
    return parts or None


def filter_events(
    events: Sequence[Any],
    *,
    since: str | None = None,
    until: str | None = None,
    tools: set[str] | None = None,
) -> list[Any]:
    """Prefer ``aggregate.filter_events`` when that module is present."""
    try:
        from datetime import date as date_cls

        agg = importlib.import_module("tok.aggregate")
        agg_filter = getattr(agg, "filter_events", None)
    except ImportError:
        agg_filter = None

    if callable(agg_filter):
        since_d = date_cls.fromisoformat(since) if since else None
        until_d = date_cls.fromisoformat(until) if until else None
        single = next(iter(tools)) if tools and len(tools) == 1 else None
        try:
            filtered = _call_supported(
                agg_filter,
                events=events,
                since=since_d,
                until=until_d,
                tool=single,
            )
            filtered = _as_event_list(filtered)
        except Exception as exc:
            print(
                f"warning: aggregate.filter_events failed ({exc}); using built-in filter",
                file=sys.stderr,
            )
            filtered = None
        if filtered is not None:
            if tools and len(tools) > 1:
                filtered = [
                    e
                    for e in filtered
                    if str(getattr(e, "tool", "") or "").lower() in tools
                ]
            return filtered

    since_dt = shanghai_midnight(since) if since else None
    until_dt = until_exclusive(until) if until else None
    out: list[Any] = []
    for event in events:
        if tools:
            tool = str(getattr(event, "tool", "") or "").lower()
            if tool not in tools:
                continue
        ts = getattr(event, "timestamp", None)
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
            if since_dt and ts < since_dt:
                continue
            if until_dt and ts >= until_dt:
                continue
        out.append(event)
    return out


# ---------------------------------------------------------------------------
# Lazy loaders
# ---------------------------------------------------------------------------

def _call_supported(fn: Callable[..., Any], **kwargs: Any) -> Any:
    """Call ``fn`` passing only kwargs its signature accepts."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn()
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return fn(**kwargs)
    accepted = {
        name
        for name, p in sig.parameters.items()
        if p.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    filtered = {k: v for k, v in kwargs.items() if k in accepted}
    if filtered:
        return fn(**filtered)
    # Single positional Path-like parameter: pass home/root as the first arg
    positional = [
        p
        for p in sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and p.default is inspect.Parameter.empty
    ]
    if len(positional) == 1:
        for key in ("home", "root", "path"):
            if key in kwargs and kwargs[key] is not None:
                return fn(kwargs[key])
    return fn()


def _as_event_list(result: Any) -> list[Any]:
    if result is None:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, tuple):
        return list(result)
    if isinstance(result, dict):
        for key in ("events", "items", "data"):
            if key in result and isinstance(result[key], list):
                return result[key]
        return []
    try:
        return list(result)
    except TypeError:
        return []


def load_events(home: Path | None = None) -> list[Any]:
    """Load usage events from all available parsers.

    Prefers ``tok.parsers.load_all_events``. If that import fails,
    each known parser is imported individually and missing ones are skipped.
    """
    kwargs: dict[str, Any] = {
        "home": home,
        "root": home,
        "path": home,
        "base": home,
    }

    try:
        parsers_mod = importlib.import_module("tok.parsers")
    except ImportError:
        parsers_mod = None

    if parsers_mod is not None:
        loader = getattr(parsers_mod, "load_all_events", None) or getattr(
            parsers_mod, "parse_all", None
        )
        if callable(loader):
            try:
                return _as_event_list(_call_supported(loader, **kwargs))
            except Exception as exc:
                print(
                    f"warning: parsers.load_all_events failed ({exc}); "
                    "trying individual parsers",
                    file=sys.stderr,
                )

    events: list[Any] = []
    missing: list[str] = []
    failed: list[str] = []
    for name in TOOLS:
        try:
            mod = importlib.import_module(f"tok.parsers.{name}")
        except ImportError:
            missing.append(name)
            continue
        parse_fn = (
            getattr(mod, "parse", None)
            or getattr(mod, "load", None)
            or getattr(mod, "load_events", None)
        )
        if not callable(parse_fn):
            missing.append(name)
            continue
        try:
            events.extend(_as_event_list(_call_supported(parse_fn, **kwargs)))
        except Exception as exc:
            failed.append(f"{name}: {exc}")
    if failed:
        print("warning: some parsers failed:", file=sys.stderr)
        for item in failed:
            print(f"  - {item}", file=sys.stderr)
    return events


def price_all(events: Sequence[Any]) -> list[Any]:
    """Price each event via ``pricing.price_event`` (lazy import)."""
    try:
        pricing = importlib.import_module("tok.pricing")
    except ImportError as exc:
        print(
            f"warning: cannot import tok.pricing ({exc}); "
            "using log-recorded costs only, unknown models stay unpriced",
            file=sys.stderr,
        )
        pricing = None

    price_fn = getattr(pricing, "price_event", None) if pricing is not None else None
    if not callable(price_fn):
        if pricing is not None:
            print(
                "warning: tok.pricing has no price_event(); "
                "using log-recorded costs only",
                file=sys.stderr,
            )
        from tok.report import price_events

        return price_events(events, None)

    costs: list[Any] = []
    for event in events:
        try:
            costs.append(price_fn(event))
        except Exception as exc:
            print(
                f"warning: price_event failed for model "
                f"{getattr(event, 'model', '?')!r}: {exc}",
                file=sys.stderr,
            )
            from tok.report import price_events as _pe

            costs.append(_pe([event], None)[0])
    return costs


def try_aggregate(
    events: Sequence[Any],
    costs: Sequence[Any],
    group_by: str,
) -> list[Any] | None:
    """Use ``tok.aggregate`` when present; otherwise return None."""
    try:
        agg = importlib.import_module("tok.aggregate")
    except ImportError:
        return None

    slug = group_by.replace("-", "_")
    names = (
        f"aggregate_by_{slug}",
        f"by_{slug}",
        f"group_by_{slug}",
        "aggregate",
    )
    fn = None
    chosen = ""
    for name in names:
        cand = getattr(agg, name, None)
        if callable(cand):
            fn = cand
            chosen = name
            break
    if fn is None:
        return None

    kwargs: dict[str, Any] = {
        "events": events,
        "costs": costs,
        "group_by": group_by,
        "priced": list(zip(events, costs, strict=False)),
    }
    try:
        result = _call_supported(fn, **kwargs)
    except Exception as exc:
        print(
            f"warning: tok.aggregate.{chosen} failed ({exc}); "
            "using built-in aggregation",
            file=sys.stderr,
        )
        return None

    if result is None:
        return None
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        rows: list[Any] = []
        for key, value in result.items():
            row = _row_from_group_value(key, value, group_by)
            if row is None:
                return None
            rows.append(row)
        _enrich_rows(rows, events, costs, group_by)
        return rows
    return None


def _row_from_group_value(key: Any, value: Any, group_by: str) -> dict[str, Any] | None:
    """Normalize aggregate.py GroupStats / dict values into a report row."""
    if isinstance(value, dict):
        row = dict(value)
    else:
        cost = getattr(value, "cost", None)
        try:
            input_tokens = int(getattr(value, "input_tokens", 0) or 0)
            output_tokens = int(getattr(value, "output_tokens", 0) or 0)
            cache_read = int(getattr(value, "cache_read_tokens", 0) or 0)
            cache_write = int(getattr(value, "cache_write_tokens", 0) or 0)
            reasoning = int(getattr(value, "reasoning_tokens", 0) or 0)
        except (TypeError, ValueError):
            return None
        usd = 0.0
        priced = True
        if cost is not None:
            try:
                usd = float(getattr(cost, "total_usd", 0.0) or 0.0)
            except (TypeError, ValueError):
                usd = 0.0
            priced = bool(getattr(cost, "priced", True))
        total = input_tokens + output_tokens + cache_read + cache_write + reasoning
        row = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "reasoning_tokens": reasoning,
            "total_tokens": total,
            "cost_usd": usd,
            "api_usd": usd,
            "sessions": int(getattr(value, "event_count", 0) or 0),
            "unpriced_tokens": 0 if priced else total,
        }
    _apply_group_key(row, group_by, key)
    return row


def _apply_group_key(row: dict[str, Any], group_by: str, key: Any) -> None:
    key_s = str(key)
    if group_by in ("tool", "summary"):
        row.setdefault("tool", key_s)
    elif group_by in ("model", "by-model"):
        row.setdefault("model", key_s)
    elif group_by in ("day", "by-day"):
        row.setdefault("day", key_s)
    elif group_by in ("project", "by-project"):
        row.setdefault("project", key_s or "(none)")
    elif group_by in ("session", "sessions"):
        row.setdefault("session_id", key_s)


def _event_group_key(event: Any, group_by: str) -> str:
    if group_by in ("tool", "summary"):
        return str(getattr(event, "tool", "") or "")
    if group_by in ("model", "by-model"):
        return str(getattr(event, "model", "") or "")
    if group_by in ("day", "by-day"):
        from tok.report import event_day

        return event_day(event)
    if group_by in ("project", "by-project"):
        return str(getattr(event, "project", None) or "")
    if group_by in ("session", "sessions"):
        return str(getattr(event, "session_id", None) or "")
    return str(getattr(event, "tool", "") or "")


def _enrich_rows(
    rows: list[Any],
    events: Sequence[Any],
    costs: Sequence[Any],
    group_by: str,
) -> None:
    """Fill days / sessions / tool / unpriced using the original events."""
    from tok.report import event_day, session_key, total_tokens_of

    buckets: dict[str, dict[str, Any]] = {}
    for event, cost in zip(events, costs, strict=False):
        key = _event_group_key(event, group_by)
        bucket = buckets.setdefault(
            key,
            {"days": set(), "sessions": set(), "tools": set(), "unpriced": 0},
        )
        bucket["days"].add(event_day(event))
        bucket["sessions"].add(session_key(event))
        tool = str(getattr(event, "tool", "") or "")
        if tool:
            bucket["tools"].add(tool)
        if cost is not None and not bool(getattr(cost, "priced", True)):
            bucket["unpriced"] += total_tokens_of(event)

    for row in rows:
        if not isinstance(row, dict):
            continue
        if group_by in ("tool", "summary"):
            key = str(row.get("tool", "") or "")
        elif group_by in ("model", "by-model"):
            key = str(row.get("model", "") or "")
        elif group_by in ("day", "by-day"):
            key = str(row.get("day", "") or "")
        elif group_by in ("project", "by-project"):
            key = str(row.get("project", "") or "")
            if key == "(none)":
                key = ""
        else:
            key = str(row.get("session_id", "") or "")
        extra = buckets.get(key)
        if not extra:
            continue
        row["days"] = len(extra["days"])
        row["sessions"] = len(extra["sessions"])
        row["unpriced_tokens"] = extra["unpriced"]
        if not row.get("tool") and extra["tools"]:
            row["tool"] = ",".join(sorted(extra["tools"]))


def collect_discover(home: Path) -> list[dict[str, Any]]:
    """List local CLI data directories via discover.py or a fallback map."""
    try:
        disc = importlib.import_module("tok.discover")
    except ImportError:
        disc = None

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    if disc is not None:
        fn = getattr(disc, "discover", None)
        if callable(fn):
            try:
                result = _call_supported(fn, home=home, root=home, path=home, base=home)
                for item in _normalize_discover_result(result):
                    key = (item["tool"], item["path"])
                    if key in seen:
                        continue
                    seen.add(key)
                    entries.append(item)
            except Exception as exc:
                print(f"warning: tok.discover.discover failed ({exc})", file=sys.stderr)

        roots = getattr(disc, "TOOL_ROOTS", None)
        if isinstance(roots, dict):
            for tool, rels in roots.items():
                for rel in rels:
                    path = home / rel
                    key = (str(tool), str(path))
                    if key in seen:
                        continue
                    seen.add(key)
                    entries.append(
                        {
                            "tool": str(tool),
                            "path": str(path),
                            "found": path.exists(),
                            "notes": _path_notes(path),
                        }
                    )

    if entries:
        entries.sort(key=lambda r: (r.get("tool", ""), not r.get("found", False), r.get("path", "")))
        return entries
    return _fallback_discover(home)


def _path_notes(path: Path) -> str:
    if not path.exists():
        return "not found"
    if path.is_file():
        return "file"
    if path.is_dir():
        try:
            n = sum(1 for _ in path.iterdir())
            return f"directory, {n} entries"
        except OSError:
            return "directory (unreadable)"
    return "present"


def _normalize_discover_result(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    items: list[Any]
    if isinstance(result, dict):
        # {tool: path}, {tool: [path, ...]}, or {tool: {path, exists}}
        items = []
        for key, value in result.items():
            if isinstance(value, dict) and not isinstance(value, Path):
                row = dict(value)
                row.setdefault("tool", key)
                items.append(row)
            elif isinstance(value, (list, tuple)):
                for part in value:
                    path = Path(part).expanduser()
                    items.append(
                        {
                            "tool": str(key),
                            "path": str(path),
                            "found": path.exists(),
                            "notes": _path_notes(path),
                        }
                    )
            else:
                path = Path(str(value)).expanduser()
                items.append(
                    {
                        "tool": str(key),
                        "path": str(path),
                        "found": path.exists(),
                        "notes": _path_notes(path),
                    }
                )
    elif isinstance(result, (list, tuple)):
        items = list(result)
    else:
        return []

    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            path = item.get("path") or item.get("root") or item.get("dir") or ""
            out.append(
                {
                    "tool": str(item.get("tool", "") or ""),
                    "path": str(path),
                    "found": bool(item.get("found", item.get("exists", False))),
                    "notes": str(item.get("notes", item.get("detail", "")) or ""),
                }
            )
        else:
            path = getattr(item, "path", getattr(item, "root", ""))
            out.append(
                {
                    "tool": str(getattr(item, "tool", "") or ""),
                    "path": str(path),
                    "found": bool(getattr(item, "found", getattr(item, "exists", False))),
                    "notes": str(getattr(item, "notes", getattr(item, "detail", "")) or ""),
                }
            )
    return out


def _fallback_discover(home: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for tool, rels in _FALLBACK_DIRS.items():
        for rel in rels:
            path = home / rel
            notes = "present" if path.exists() else "not found"
            if path.exists() and path.is_dir():
                try:
                    n = sum(1 for _ in path.iterdir())
                    notes = f"directory, {n} entries"
                except OSError:
                    notes = "directory (unreadable)"
            entries.append(
                {
                    "tool": tool,
                    "path": str(path),
                    "found": path.exists(),
                    "notes": notes,
                }
            )
    return entries


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _print_no_data(*, json_output: bool, file: TextIO) -> None:
    if json_output:
        json.dump(
            {
                "rows": [],
                "totals": {},
                "unpriced_tokens": 0,
                "message": _NO_DATA,
            },
            file,
            indent=2,
            ensure_ascii=False,
        )
        file.write("\n")
        return
    file.write(_NO_DATA + "\n")
    file.write("\n")
    file.write("Hints:\n")
    file.write("  - Run `tok w` to see which local CLI data directories exist.\n")
    file.write("  - Use `-H /path` (or TOK_HOME) if data lives under another home.\n")
    file.write("  - Supported tools: " + ", ".join(TOOLS) + ".\n")


_PRINTERS: dict[str, Callable[..., None]] = {
    "summary": print_summary,
    "by-model": print_by_model,
    "by-day": print_by_day,
    "by-project": print_by_project,
    "sessions": print_sessions,
}

_GROUP_BY = {
    "summary": "tool",
    "by-model": "model",
    "by-day": "day",
    "by-project": "project",
    "sessions": "session",
}


def run_command(args: argparse.Namespace, *, file: TextIO | None = None) -> int:
    out = file or sys.stdout
    command = _CANONICAL_COMMAND.get(args.command, args.command or "summary")
    home = resolve_home(getattr(args, "home", None))
    json_output = bool(getattr(args, "json_output", False))

    # Parsers and discover honor TOK_HOME (see discover.default_home).
    if getattr(args, "home", None):
        os.environ["TOK_HOME"] = str(home)
        os.environ["AI_USAGE_HOME"] = str(home)

    if command == "discover":
        entries = collect_discover(home)
        print_discover(entries, json_output=json_output, file=out)
        return 0

    if command not in _PRINTERS:
        print(f"error: unknown command {command!r}", file=sys.stderr)
        return 2

    tools = parse_tool_filter(getattr(args, "tool", None))
    try:
        events = load_events(home)
    except Exception as exc:
        print(f"error: failed to load usage events: {exc}", file=sys.stderr)
        if os.environ.get("AI_USAGE_DEBUG"):
            traceback.print_exc()
        return 1

    events = filter_events(
        events,
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        tools=tools,
    )

    if not events:
        _print_no_data(json_output=json_output, file=out)
        return 0

    costs = price_all(events)
    group_by = _GROUP_BY[command]
    rows = try_aggregate(events, costs, group_by)
    printer = _PRINTERS[command]
    if rows is not None:
        printer(rows=rows, json_output=json_output, file=out)
    else:
        printer(events, costs, json_output=json_output, file=out)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 0

    try:
        return run_command(args)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        if os.environ.get("AI_USAGE_DEBUG"):
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
