"""Cross-platform discovery and timezone tests."""

from __future__ import annotations

import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tok.discover import discover, extra_platform_roots  # noqa: E402
from tok.parsers._common import default_roots, resolve_tool_roots  # noqa: E402


def test_asia_shanghai_zoneinfo_and_report_import() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    assert str(tz) == "Asia/Shanghai"
    import tok.report

    assert tok.report.SHANGHAI.key == "Asia/Shanghai" or str(tok.report.SHANGHAI) == "Asia/Shanghai"


def test_discover_lists_xdg_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xdg = tmp_path / ".config" / "claude"
    xdg.mkdir(parents=True)
    monkeypatch.delenv("AI_USAGE_HOME", raising=False)
    monkeypatch.setenv("TOK_HOME", str(tmp_path))
    found = discover()
    assert "claude" in found
    resolved = {p.resolve() for p in found["claude"]}
    assert xdg.resolve() in resolved


def test_discover_lists_copilot_dotdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copilot = tmp_path / ".copilot"
    copilot.mkdir()
    monkeypatch.delenv("AI_USAGE_HOME", raising=False)
    monkeypatch.setenv("TOK_HOME", str(tmp_path))
    found = discover()
    assert "copilot" in found
    assert any(p.resolve() == copilot.resolve() for p in found["copilot"])


def test_extra_platform_roots_skipped_when_tok_home_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sandbox / TOK_HOME must not pick up real-user AppData or Library dirs."""
    monkeypatch.delenv("AI_USAGE_HOME", raising=False)
    monkeypatch.setenv("TOK_HOME", str(tmp_path))
    real_home = Path.home().resolve()
    override = tmp_path.resolve()

    for tool in ("claude", "opencode", "amp", "copilot"):
        extras = extra_platform_roots(tool)
        assert extras == []
        for path in extras:
            resolved = path.resolve()
            assert override == resolved or override in resolved.parents
            if real_home != override:
                assert real_home != resolved
                assert real_home not in resolved.parents

    found = discover()
    for paths in found.values():
        for path in paths:
            resolved = path.resolve()
            assert override == resolved or override in resolved.parents

    for path in default_roots(".opencode", ".local/share/opencode"):
        resolved = path.resolve()
        assert override == resolved or override in resolved.parents
    for path in resolve_tool_roots(None, ".amp", ".local/share/amp"):
        resolved = path.resolve()
        assert override == resolved or override in resolved.parents


def test_discover_explicit_home_does_not_add_platform_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TOK_HOME", raising=False)
    monkeypatch.delenv("AI_USAGE_HOME", raising=False)
    (tmp_path / ".config" / "claude").mkdir(parents=True)
    found = discover(tmp_path)
    assert "claude" in found
    override = tmp_path.resolve()
    for path in found["claude"]:
        resolved = path.resolve()
        assert override == resolved or override in resolved.parents
