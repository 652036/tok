"""Locate local AI-CLI data directories under a home path."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

# Relative to default_home(). Values may be directories or files.
# aider: .aider.chat.history.md is often per-repo, not only under $HOME.
TOOL_ROOTS: dict[str, list[str]] = {
    "claude": [".claude", ".claude.json"],
    "codex": [".codex"],
    "grok": [".grok", ".config/grok", ".xai"],
    "gemini": [".gemini", ".config/gemini"],
    "aider": [".aider", ".aider.chat.history.md"],
    "opencode": [".opencode", ".config/opencode", ".local/share/opencode"],
    "amp": [".amp", ".config/amp"],
    "copilot": [".config/github-copilot"],
}


def default_home() -> Path:
    """``$AI_USAGE_HOME`` if set, otherwise ``Path.home()``."""
    override = os.environ.get("AI_USAGE_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home()


def discover(home: Path | None = None) -> dict[str, list[Path]]:
    """Return tool -> existing candidate paths (files or directories)."""
    base = Path(home) if home is not None else default_home()
    found: dict[str, list[Path]] = {}
    for tool, rels in TOOL_ROOTS.items():
        existing = [base / rel for rel in rels if (base / rel).exists()]
        if existing:
            found[tool] = existing
    return found


def iter_files(root: Path, patterns: tuple[str, ...]) -> Iterator[Path]:
    """Yield files under *root* matching any glob in *patterns*.

    If *root* is a file, yield it when its name matches a pattern (or when
    *patterns* is empty). Missing paths yield nothing. Deduplicates overlaps.
    """
    root = Path(root)
    if not root.exists():
        return
    if root.is_file():
        if not patterns or any(root.match(p) or root.name == p for p in patterns):
            yield root
        return

    seen: set[Path] = set()
    for pattern in patterns:
        for path in root.rglob(pattern):
            if not path.is_file():
                continue
            key = path.resolve()
            if key in seen:
                continue
            seen.add(key)
            yield path
