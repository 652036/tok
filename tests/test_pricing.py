"""Bundled LiteLLM price table lookups."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tok.pricing import get_rate, reload_table  # noqa: E402


def _assert_priced(model: str) -> None:
    rate = get_rate(model)
    assert rate is not None, f"no rate for {model}"
    inp = rate.get("input")
    out = rate.get("output")
    assert inp is not None and float(inp) > 0, f"{model} input={inp}"
    assert out is not None and float(out) > 0, f"{model} output={out}"


def test_get_rate_known_models() -> None:
    reload_table()
    _assert_priced("claude-sonnet-4-20250514")
    _assert_priced("gpt-4o")
    _assert_priced("gemini-2.5-pro")
