# Contributing to tok

Thanks for helping keep local, multi-CLI token/cost stats accurate and private.

## Setup

Requirements: **Python 3.11+**.

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Run the checks CI runs:

```bash
ruff check
pytest -q
```

## Adding a parser

Each tool lives in `src/tok/parsers/` and exposes:

```python
def parse(root: Path | None = None) -> list[UsageEvent]:
    ...
```

Follow the shared `UsageEvent` contract in `src/tok/models.py` (do not rename fields). Discover the default data root, and accept an override path.

**Privacy is the contract:**

- Usage metadata only (tokens, model, timestamps, session ids, recorded cost).
- Never read, store, or print API keys, credentials, or message/conversation content.

A parser PR should include:

1. The parser module.
2. Tests under `tests/` that use fixtures, not real user logs.
3. Redacted samples under `sample_data/` (no secrets, no transcripts).

See [docs/SOURCES.md](docs/SOURCES.md) for where each CLI stores usage data.

## Pricing updates

Do not invent official prices. If a model is missing, leave it unpriced (`priced=False`) and still count tokens.

Rates in `src/tok/pricing_data.json` come from the LiteLLM-style dump in `docs/vendor/` (same source sub2API uses). Refresh with `scripts/import_litellm_prices.py` — do not hand-edit invented numbers. See [docs/PRICING_SOURCES.md](docs/PRICING_SOURCES.md).

- Prefer `raw_cost_usd` from the log when present; otherwise use the table.

## Pull request expectations

- Tests pass (`pytest -q`) and `ruff check` is clean.
- No secrets, API keys, `.env` files, or real user logs.
- Do not paste conversation transcripts or raw CLI dumps into the PR or issue.
- Keep samples in `sample_data/` redacted.

## Code style

Lint with **ruff** (line length 100, target Python 3.11). See `[tool.ruff]` in `pyproject.toml`.
