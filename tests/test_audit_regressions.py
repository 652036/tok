"""Regression tests for rate matching and independent group accumulators."""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tok import pricing  # noqa: E402
from tok.aggregate import GroupStats  # noqa: E402
from tok.models import CostBreakdown, UsageEvent  # noqa: E402


def _event(model: str = "example") -> UsageEvent:
    return UsageEvent(
        tool="test", timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        model=model, project="fixture", input_tokens=10,
    )


@pytest.mark.parametrize("key", ["example-mini", "vendor-example"])
def test_incomplete_model_name_is_not_priced(monkeypatch, key: str) -> None:
    # Synthetic fixture values, not real-world model prices.
    monkeypatch.setattr(pricing, "_load_table", lambda: {"models": {key: {"input": 1}}})
    assert pricing.get_rate("example") is None
    cost = pricing.price_event(_event())
    assert not cost.priced
    assert cost.source == "unknown"


@pytest.mark.parametrize("model", ["example-v1-20260101", "region-example-v1"])
def test_documented_bounded_matches_still_work(monkeypatch, model: str) -> None:
    rate = {"input": 1}
    monkeypatch.setattr(pricing, "_load_table", lambda: {"models": {"example-v1": rate}})
    assert pricing.get_rate(model) is rate


def test_exact_match_takes_priority_over_longer_variant(monkeypatch) -> None:
    exact = {"input": 1}
    table = {"example-mini": {"input": 2}, "example": exact}
    monkeypatch.setattr(pricing, "_load_table", lambda: {"models": table})
    assert pricing.get_rate("example") is exact


def test_longest_forward_match_and_alias_normalization(monkeypatch) -> None:
    specific = {"input": 2, "aliases": ["Example_Pro"]}
    table = {"example": {"input": 1}, "canonical-pro": specific}
    monkeypatch.setattr(pricing, "_load_table", lambda: {"models": table})
    assert pricing.get_rate("provider/EXAMPLE_PRO-20260101") is specific


def test_group_does_not_mutate_supplied_cost() -> None:
    cost = CostBreakdown(input_usd=1, total_usd=1, source="log")
    original = replace(cost)
    stats = GroupStats("first")
    stats.add_event(_event(), cost)
    stats.add_event(_event(), CostBreakdown(input_usd=2, total_usd=2))
    assert cost == original
    assert stats.cost.total_usd == 3
    assert stats.cost.source == "mixed"


def test_groups_using_same_cost_are_independent() -> None:
    shared = CostBreakdown(total_usd=1, source="log")
    first, second = GroupStats("first"), GroupStats("second")
    first.add_event(_event(), shared)
    second.add_event(_event(), shared)
    first.add_event(_event(), CostBreakdown(total_usd=2, source="log"))
    assert first.cost.total_usd == 3
    assert second.cost.total_usd == 1
    assert shared.total_usd == 1


def test_mutating_caller_cost_does_not_change_group() -> None:
    cost = CostBreakdown(total_usd=1, priced=False, source="unknown")
    stats = GroupStats("first")
    stats.add_event(_event(), cost)
    cost.total_usd = 99
    assert stats.cost.total_usd == 1
    assert not stats.cost.priced
    assert stats.cost.source == "unknown"
