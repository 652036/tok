"""Price a UsageEvent from a recorded log cost or the bundled rate table."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from tok.models import CostBreakdown, UsageEvent

_PER_MILLION = 1_000_000.0


@lru_cache(maxsize=1)
def _load_table() -> dict[str, Any]:
    """Load pricing_data.json from the package. Missing file -> empty table."""
    empty: dict[str, Any] = {
        "updated": "",
        "currency": "USD",
        "unit": "per_1m_tokens",
        "models": {},
    }
    try:
        ref = resources.files("tok").joinpath("pricing_data.json")
        with ref.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, OSError, json.JSONDecodeError, ModuleNotFoundError):
        return empty
    if not isinstance(data, dict):
        return empty
    models = data.get("models")
    if not isinstance(models, dict):
        data["models"] = {}
    return data


def reload_table() -> dict[str, Any]:
    """Drop the cached table (useful after pricing_data.json is updated)."""
    _load_table.cache_clear()
    return _load_table()


def _norm(name: str) -> str:
    name = name.strip().lower().replace("_", "-").replace(" ", "-")
    while "--" in name:
        name = name.replace("--", "-")
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    return name


def _rate_aliases(key: str, rate: dict[str, Any]) -> list[str]:
    aliases = [_norm(key)]
    extra = rate.get("aliases") if isinstance(rate, dict) else None
    if isinstance(extra, list):
        aliases.extend(_norm(a) for a in extra if isinstance(a, str))
    return aliases


def get_rate(model: str) -> dict | None:
    """Return the per-1M rate dict for *model*, or None if unknown.

    Matching is case-insensitive. Provider prefixes (``vendor/model``) are
    stripped. A table key matches when it equals the model or is a hyphen-
    bounded prefix/suffix (``claude-sonnet-4-20250514`` → ``claude-sonnet-4``).
    The longest matching key wins so ``gpt-4`` does not steal ``gpt-4o``.
    """
    models = _load_table().get("models") or {}
    if not models or not model:
        return None

    needle = _norm(model)
    exact: dict[str, Any] | None = None
    best: dict[str, Any] | None = None
    best_len = -1

    for key, rate in models.items():
        if not isinstance(rate, dict):
            continue
        for alias in _rate_aliases(str(key), rate):
            if not alias:
                continue
            if alias == needle:
                exact = rate
                break
            # Only decorate a known model, never infer one from a shorter name.
            bounded = (
                needle.startswith(alias + "-")
                or needle.endswith("-" + alias)
            )
            if bounded and len(alias) > best_len:
                best = rate
                best_len = len(alias)
        if exact is not None:
            return exact
    return best


def _tokens_usd(tokens: int, rate_per_m: object) -> float:
    if rate_per_m is None or tokens == 0:
        return 0.0
    try:
        rate = float(rate_per_m)
    except (TypeError, ValueError):
        return 0.0
    return tokens * rate / _PER_MILLION


def price_event(event: UsageEvent) -> CostBreakdown:
    """Price one event.

    Prefer ``event.raw_cost_usd`` (source=``log``, component split unknown).
    Otherwise apply per-1M table rates. Unknown models stay at $0 with
    ``priced=False`` / ``source="unknown"``.

    Reasoning tokens are a separate field. If the table's ``reasoning`` rate
    is null, those tokens are billed at the output rate — they are not added
    into ``output_tokens``.
    """
    if event.raw_cost_usd is not None:
        return CostBreakdown(
            total_usd=float(event.raw_cost_usd),
            priced=True,
            source="log",
        )

    rate = get_rate(event.model)
    if rate is None:
        return CostBreakdown(priced=False, source="unknown")

    reasoning_rate = rate.get("reasoning")
    if reasoning_rate is None:
        reasoning_rate = rate.get("output")

    input_usd = _tokens_usd(event.input_tokens, rate.get("input"))
    output_usd = _tokens_usd(event.output_tokens, rate.get("output"))
    cache_read_usd = _tokens_usd(event.cache_read_tokens, rate.get("cache_read"))
    cache_write_usd = _tokens_usd(event.cache_write_tokens, rate.get("cache_write"))
    reasoning_usd = _tokens_usd(event.reasoning_tokens, reasoning_rate)
    total = input_usd + output_usd + cache_read_usd + cache_write_usd + reasoning_usd
    return CostBreakdown(
        input_usd=input_usd,
        output_usd=output_usd,
        cache_read_usd=cache_read_usd,
        cache_write_usd=cache_write_usd,
        reasoning_usd=reasoning_usd,
        total_usd=total,
        priced=True,
        source="table",
    )
