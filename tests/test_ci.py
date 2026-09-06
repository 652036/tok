"""CI workflow and README build-badge checks."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ACTIONS_BADGE = "https://github.com/652036/tok/actions/workflows/ci.yml/badge.svg"
ACTIONS_URL = "https://github.com/652036/tok/actions/workflows/ci.yml"


def test_ci_workflow_runs_ruff_and_pytest() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert WORKFLOW.is_file()
    assert "ruff check" in text
    assert "pytest -q" in text
    assert 'pip install -e ".[dev]"' in text


def test_readmes_use_live_actions_badge() -> None:
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "build-placeholder" not in text
        assert ACTIONS_BADGE in text
        assert ACTIONS_URL in text
