#!/usr/bin/env python3
"""Import LiteLLM-style prices into src/ai_usage/pricing_data.json.

Same snapshot source sub2API uses (Wei-Shaw/model-price-repo, fallback
BerriAI/litellm). LiteLLM stores USD *per token*; we store USD *per 1M tokens*.

This script does not invent rates. It only converts fields that exist in the
vendor JSON checked into docs/vendor/.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "docs" / "vendor" / "model_prices_and_context_window.json"
DEFAULT_OUTPUT = REPO_ROOT / "src" / "ai_usage" / "pricing_data.json"

WEI_SHAW_URL = (
    "https://raw.githubusercontent.com/Wei-Shaw/model-price-repo/"
    "main/model_prices_and_context_window.json"
)
LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/"
    "main/model_prices_and_context_window.json"
)

UPDATED = "2026-08-18"

# Always import these modes when at least one token price is present.
INCLUDE_MODES = {"chat", "completion", "responses"}
# Import these only when *both* input and output token prices exist.
CONDITIONAL_MODES = {
    "embedding",
    "image_generation",
    "audio",
    "audio_speech",
    "audio_transcription",
    "audio_generation",
    "realtime",
}

PROVIDER_MAP = {
    "vertex_ai-language-models": "google",
    "vertex_ai-embedding-models": "google",
    "vertex_ai-vision-models": "google",
    "vertex_ai-chat-models": "google",
    "vertex_ai": "google",
    "gemini": "google",
    "text-completion-openai": "openai",
}

# -20250514 or -2025-11-13 at the end of a model id
_DATE_SUFFIX = re.compile(r"(-\d{4}-\d{2}-\d{2}|-\d{8})$")


def per_million(value: object) -> float | None:
    """LiteLLM per-token USD → per-1M USD. None if missing/unparseable."""
    if value is None:
        return None
    try:
        n = float(value) * 1_000_000.0
    except (TypeError, ValueError):
        return None
    n = round(n, 6)
    if n == 0.0:
        return 0.0
    return n


def field_per_million(spec: dict[str, Any], key: str) -> float | None:
    if key not in spec:
        return None
    return per_million(spec[key])


def normalize_provider(raw: object, model_id: str) -> str:
    provider = str(raw).strip() if raw else ""
    if provider in PROVIDER_MAP:
        return PROVIDER_MAP[provider]
    if provider.startswith("vertex_ai"):
        return "google"
    # Bedrock Anthropic SKUs stay bedrock (e.g. claude-*-v1:0).
    if provider == "bedrock":
        return "bedrock"
    return provider or "unknown"


def include_entry(key: str, spec: Any) -> bool:
    if key.startswith("_"):
        return False
    if not isinstance(spec, dict):
        return False
    has_in = "input_cost_per_token" in spec
    has_out = "output_cost_per_token" in spec
    if not has_in and not has_out:
        return False
    mode = str(spec.get("mode") or "").strip()
    if mode in INCLUDE_MODES:
        return True
    if mode in CONDITIONAL_MODES or mode.startswith(("audio", "image")):
        return has_in and has_out
    # Unknown mode: keep if it has a token price (same bar as chat).
    return True


def aliases_for(model_id: str, provider: str) -> list[str]:
    aliases: list[str] = []
    match = _DATE_SUFFIX.search(model_id)
    if match:
        stem = model_id[: match.start()]
        if stem and stem != model_id:
            aliases.append(stem)
    if provider and "/" not in model_id:
        prefixed = f"{provider}/{model_id}"
        if prefixed != model_id:
            aliases.append(prefixed)
    seen: set[str] = set()
    out: list[str] = []
    for alias in aliases:
        if alias and alias not in seen and alias != model_id:
            seen.add(alias)
            out.append(alias)
    return out


def convert(vendor: dict[str, Any]) -> dict[str, dict[str, Any]]:
    models: dict[str, dict[str, Any]] = {}
    for key, spec in vendor.items():
        if not include_entry(str(key), spec):
            continue
        assert isinstance(spec, dict)
        provider = normalize_provider(spec.get("litellm_provider"), str(key))
        models[str(key)] = {
            "provider": provider,
            "input": field_per_million(spec, "input_cost_per_token"),
            "output": field_per_million(spec, "output_cost_per_token"),
            "cache_read": field_per_million(spec, "cache_read_input_token_cost"),
            "cache_write": field_per_million(spec, "cache_creation_input_token_cost"),
            "reasoning": field_per_million(spec, "output_cost_per_reasoning_token"),
            "aliases": aliases_for(str(key), provider),
        }
    return models


def build_table(
    vendor: dict[str, Any],
    *,
    source: str,
    updated: str,
) -> dict[str, Any]:
    models = convert(vendor)
    return {
        "updated": updated,
        "currency": "USD",
        "unit": "per_1m_tokens",
        "source": source,
        "model_count": len(models),
        "models": dict(sorted(models.items())),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Vendor LiteLLM JSON")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="pricing_data.json")
    p.add_argument("--source", default=WEI_SHAW_URL, help="Recorded source URL")
    p.add_argument("--updated", default=UPDATED, help="ISO date written into the table")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path: Path = args.input
    if not path.is_file():
        print(f"error: vendor JSON not found: {path}", file=sys.stderr)
        print(
            f"download {WEI_SHAW_URL} (or {LITELLM_URL}) into {DEFAULT_INPUT}",
            file=sys.stderr,
        )
        return 1
    vendor = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(vendor, dict):
        print("error: vendor JSON must be an object", file=sys.stderr)
        return 1
    table = build_table(vendor, source=args.source, updated=args.updated)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(table, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    counts = Counter(row["provider"] for row in table["models"].values())
    print(f"wrote {args.output}  models={table['model_count']}  source={args.source}")
    print("top providers:")
    for name, n in counts.most_common(15):
        print(f"  {n:5d}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
