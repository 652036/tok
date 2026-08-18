"""Aider local usage parser.

Targeted path / schema (researched 2026-08, Aider-AI/aider):

Default roots (under ``$TOK_HOME`` or ``Path.home()``) — **home-level
only**. We do *not* recursively scan the whole home directory for
``.aider.chat.history.md`` (that is too slow on a real machine)::

    ~/.aider
    ~/.config/aider

If ``root`` is passed, that path is searched for common history filenames
*and* json/jsonl/log files (project checkouts often keep history at the
repo root).

If ``$AIDER_HISTORY`` is set, that file is always parsed (in addition to
the resolved roots).

Primary text format (``.aider.chat.history.md`` and stdout transcripts)::

    Main model: openai/gpt-4o with diff edit format
    # aider chat started at 2026-03-01 10:15:00
    > Tokens: 12k sent, 133 received. Cost: $0.04 message, $0.10 session.
    Tokens: 216k sent, 108k cache write, 1.4k received. Cost: $0.75 message, $0.79 session.
    Tokens: 11,740 sent, 2,012 received.

Counts may be plain ints, comma-grouped, or ``k`` / ``M`` suffixed.
``sent`` → input_tokens, ``received`` → output_tokens.
``cache write`` / ``cache read`` → cache_write_tokens / cache_read_tokens.
``Cost: $X message`` is the per-turn USD cost stored on the event
(session cost is cumulative and is *not* copied, to avoid double-counting).

Assumed JSON sidecar keys (no official JSON schema; best-effort)::

    sent / received / input_tokens / output_tokens / cost / model

Only token/cost/model lines are kept. User/assistant prose in the markdown
history is ignored and never stored.
"""

from __future__ import annotations

import os
import re
from datetime import timezone
from pathlib import Path

from tok.models import UsageEvent
from tok.parsers._common import (
    file_mtime,
    iter_files,
    iter_json_objects,
    make_event,
    parse_timestamp,
    resolve_tool_roots,
)

TOOL = "aider"

HISTORY_NAMES = {
    ".aider.chat.history.md",
    ".aider.history.md",
    "aider.chat.history.md",
    ".aider.chat.history",
}

# ``.aider.input.history`` is the command readline history — skip it.

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_STARTED_RE = re.compile(
    r"aider chat started at\s+(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?)",
    re.IGNORECASE,
)
_MODEL_RE = re.compile(
    r"^(?:main\s+)?model:\s+(\S+)",
    re.IGNORECASE,
)
_PROJECT_RE = re.compile(r"^#{2,4}\s+(\S+)")
# Token counts: 12k / 1.1k / 1,234 / 2.7M / 133
_COUNT = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?[kKmM]?)"
_TOKENS_RE = re.compile(
    rf"Tokens:\s*{_COUNT}\s+sent"
    rf"(?:,\s*{_COUNT}\s+cache\s+write)?"
    rf"(?:,\s*{_COUNT}\s+cache\s+read)?"
    rf",\s*{_COUNT}\s+received",
    re.IGNORECASE,
)
# Alternate order seen in some versions: sent, received, then cache.
_TOKENS_ALT_RE = re.compile(
    rf"Tokens:\s*{_COUNT}\s+sent,\s*{_COUNT}\s+received"
    rf"(?:,\s*{_COUNT}\s+cache\s+write)?"
    rf"(?:,\s*{_COUNT}\s+cache\s+read)?",
    re.IGNORECASE,
)
_COST_MSG_RE = re.compile(
    r"Cost:\s*\$?\s*([0-9]*\.?[0-9]+)\s+message",
    re.IGNORECASE,
)


def parse(root: Path | None = None) -> list[UsageEvent]:
    """Parse aider history / sidecar logs. Returns ``[]`` if nothing is found."""
    extra_names = HISTORY_NAMES
    roots = resolve_tool_roots(
        root,
        ".aider",
        ".config/aider",
        undotted_name="aider",
        extra_home_files=tuple(sorted(HISTORY_NAMES)),
    )

    hist = os.environ.get("AIDER_HISTORY")
    if hist:
        roots.append(Path(hist).expanduser())

    events: list[UsageEvent] = []
    seen: set[Path] = set()
    for path in iter_files(
        roots,
        extra_names=extra_names,
        extra_suffixes=(".md",),
    ):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            events.extend(_parse_file(path))
        except OSError:
            continue
    return events


def _parse_file(path: Path) -> list[UsageEvent]:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in {".json", ".jsonl", ".log"}:
        return _parse_json_file(path)
    if suffix == ".md" or name in HISTORY_NAMES or "history" in name:
        return _parse_history_text(path)
    return []


def _parse_json_file(path: Path) -> list[UsageEvent]:
    events: list[UsageEvent] = []
    for obj in iter_json_objects(path):
        if not isinstance(obj, dict):
            continue
        ev = make_event(TOOL, path, obj, extra={"format": "aider-json"})
        if ev is not None:
            events.append(ev)
    return events


def _parse_history_text(path: Path) -> list[UsageEvent]:
    text = None
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not text:
        return []

    model = "unknown"
    project: str | None = _project_from_path(path)
    current_ts = file_mtime(path)
    events: list[UsageEvent] = []

    for raw_line in text.splitlines():
        line = _ANSI_RE.sub("", raw_line).strip()
        if not line:
            continue

        started = _STARTED_RE.search(line)
        if started:
            current_ts = parse_timestamp(started.group(1), current_ts)
            continue

        model_match = _MODEL_RE.search(line.lstrip("> ").lstrip("# ").strip())
        if model_match and "token" not in model_match.group(1).lower():
            model = model_match.group(1).strip().rstrip(",")
            continue

        if line.startswith("####") or line.startswith("### "):
            proj_match = _PROJECT_RE.match(line)
            if proj_match:
                label = proj_match.group(1).strip()
                if label and not label.lower().startswith("aider"):
                    project = Path(label).name or label
            continue

        parsed = _parse_token_line(line)
        if parsed is None:
            continue
        input_tokens, output_tokens, cache_write, cache_read, cost = parsed
        ts = current_ts if current_ts.tzinfo else current_ts.replace(tzinfo=timezone.utc)
        ev = UsageEvent(
            tool=TOOL,
            timestamp=ts,
            model=model,
            project=project,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            reasoning_tokens=0,
            raw_cost_usd=cost,
            source_path=str(path),
            session_id=None,
            extra={"format": "aider-history"},
        )
        events.append(ev)
    return events


def _parse_token_line(line: str) -> tuple[int, int, int, int, float | None] | None:
    if "Tokens:" not in line and "tokens:" not in line:
        return None
    match = _TOKENS_RE.search(line)
    cache_write = 0
    cache_read = 0
    if match:
        sent = _parse_count(match.group(1))
        if match.group(2) is not None:
            cache_write = _parse_count(match.group(2))
        if match.group(3) is not None:
            cache_read = _parse_count(match.group(3))
        received = _parse_count(match.group(4))
    else:
        alt = _TOKENS_ALT_RE.search(line)
        if not alt:
            return None
        sent = _parse_count(alt.group(1))
        received = _parse_count(alt.group(2))
        if alt.group(3) is not None:
            cache_write = _parse_count(alt.group(3))
        if alt.group(4) is not None:
            cache_read = _parse_count(alt.group(4))

    cost = None
    cost_match = _COST_MSG_RE.search(line)
    if cost_match:
        try:
            cost = float(cost_match.group(1))
        except ValueError:
            cost = None
    return sent, received, cache_write, cache_read, cost


def _parse_count(raw: str) -> int:
    s = raw.strip().replace(",", "")
    if not s:
        return 0
    mult = 1.0
    if s[-1] in "kK":
        mult = 1_000.0
        s = s[:-1]
    elif s[-1] in "mM":
        mult = 1_000_000.0
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def _project_from_path(path: Path) -> str | None:
    # If the history file lives in a project checkout, use that folder name.
    parent = path.parent.name
    if parent in {".aider", "aider", ""}:
        return None
    return parent
