"""Locate local AI-CLI data directories under a home path."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

# Relative to default_home(). Values may be directories or files.
# aider: .aider.chat.history.md is often per-repo, not only under $HOME.
TOOL_ROOTS: dict[str, list[str]] = {
    "claude": [".config/claude", ".claude", ".claude.json"],
    "codex": [".codex"],
    "grok": [".grok", ".config/grok", ".xai"],
    "gemini": [".gemini", ".config/gemini"],
    "aider": [".aider", ".aider.chat.history.md"],
    "opencode": [".opencode", ".config/opencode", ".local/share/opencode"],
    "amp": [".amp", ".config/amp"],
    "copilot": [".copilot", ".config/github-copilot"],
}


def env_home_override() -> str | None:
    """Preferred ``$TOK_HOME``, then deprecated ``$AI_USAGE_HOME``."""
    for name in ("TOK_HOME", "AI_USAGE_HOME"):
        value = os.environ.get(name)
        if value:
            return value
    return None


def default_home() -> Path:
    """``$TOK_HOME`` (preferred) or ``$AI_USAGE_HOME``, else ``Path.home()``."""
    override = env_home_override()
    if override:
        return Path(override).expanduser()
    return Path.home()


def extra_platform_roots(tool: str) -> list[Path]:
    """Windows / macOS locations that are not Unix home-relative paths.

    Only cited official / ccusage paths belong here. When ``$TOK_HOME`` or
    ``$AI_USAGE_HOME`` is set, return nothing so tests and sandboxes never
    scan a real user AppData / Library tree.

    Citations (why nothing extra is added today):

    * OpenCode Windows session data is ``%USERPROFILE%\\.local\\share\\opencode``
      (https://opencode.ai/docs/troubleshooting/) — already ``.local/share/opencode``
      under ``Path.home()``. Official docs once mentioned ``%APPDATA%\\opencode``;
      that is desktop UI state, not usage logs (https://github.com/sst/opencode/issues/702).
    * Amp: ccusage documents ``${AMP_DATA_DIR:-~/.local/share/amp}`` only
      (https://github.com/ccusage/ccusage/blob/main/docs/guide/amp/index.md).
    * Claude Code official Windows home is ``%USERPROFILE%\\.claude``
      (https://code.claude.com/docs/en/claude-directory.md). Do **not** add
      ``~/Library/Application Support/Claude`` (Claude Desktop, not Claude Code).
    """
    if env_home_override():
        return []
    # No extra AppData / Library roots are currently cited. Home-relative
    # Unix paths (including %USERPROFILE%\.local\share\opencode on Windows)
    # are listed in TOOL_ROOTS instead.
    cited: dict[str, list[Path]] = {}
    return list(cited.get(tool, []))


def discover(home: Path | None = None) -> dict[str, list[Path]]:
    """Return tool -> existing candidate paths (files or directories)."""
    base = Path(home) if home is not None else default_home()
    found: dict[str, list[Path]] = {}
    for tool, rels in TOOL_ROOTS.items():
        existing = [base / rel for rel in rels if (base / rel).exists()]
        # extras only on the real-user default lookup (no explicit home).
        # extra_platform_roots() is already empty under TOK_HOME.
        if home is None:
            for extra in extra_platform_roots(tool):
                if extra.exists() and extra not in existing:
                    existing.append(extra)
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
