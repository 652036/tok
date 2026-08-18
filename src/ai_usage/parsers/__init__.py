"""Best-effort parsers for local AI CLI usage logs.

``PARSERS`` is filled as sibling modules appear. ``load_all_events`` imports
them lazily and skips modules that are not ready yet.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_usage.models import UsageEvent

# tool name -> parse(root) callable. Other agents may populate this.
PARSERS: dict[str, Callable[..., list]] = {}

# Import order matches DESIGN.md. copilot is included if/when it exists.
_PARSER_MODULES: tuple[str, ...] = (
    "claude",
    "codex",
    "grok",
    "gemini",
    "aider",
    "opencode",
    "amp",
    "copilot",
)


def _try_load(name: str) -> Callable[..., list] | None:
    if name in PARSERS:
        return PARSERS[name]
    try:
        module = importlib.import_module(f"ai_usage.parsers.{name}")
        parse = getattr(module, "parse")
    except (ImportError, AttributeError):
        return None
    PARSERS[name] = parse
    return parse


def load_all_events(
    home: Path | None = None,
    tools: Iterable[str] | None = None,
) -> list[UsageEvent]:
    """Collect ``UsageEvent``s from every available parser.

    Imports ``claude``, ``codex``, ``grok``, ``gemini``, ``aider``,
    ``opencode``, ``amp`` (and ``copilot`` if present) lazily. Missing or
    incomplete modules are skipped (``ImportError`` / ``AttributeError``).
    """
    from ai_usage.discover import default_home
    from ai_usage.models import UsageEvent as _UsageEvent

    root = Path(home) if home is not None else default_home()
    wanted = {t.lower() for t in tools} if tools is not None else None
    events: list[_UsageEvent] = []

    # Prefer explicit imports so parallel parser files are picked up even
    # when PARSERS has not been populated yet.
    for name in _PARSER_MODULES:
        if wanted is not None and name not in wanted:
            continue
        parse = _try_load(name)
        if parse is None:
            continue
        parsed = parse(root)
        if parsed:
            events.extend(parsed)
    return events


__all__ = [
    "PARSERS",
    "load_all_events",
]
