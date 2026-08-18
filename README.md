# tok

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Build](https://img.shields.io/badge/build-placeholder-lightgrey.svg)](#development)

Local token and API-equivalent cost stats for AI coding CLIs.

`tok` reads **usage metadata already stored on your machine** by tools such as Claude Code, Codex, Grok, Gemini CLI, Aider, OpenCode, Amp, and GitHub Copilot CLI. It counts tokens and converts them to a **USD estimate using public API list prices**. Nothing is uploaded.

[中文文档](README.zh-CN.md)

---

## What it is

A small Python 3.11+ command-line tool. You run it on your laptop; it walks well-known local data directories, parses usage events, and prints tables (or JSON).

It answers questions like:

- How many tokens did I burn this month, by tool and by model?
- What would that have cost at official API list prices?
- Which project or session used the most?

## Why

Subscription dashboards are incomplete, split across vendors, or missing entirely for local CLIs. Logs on disk already contain token counts (and sometimes a recorded cost). `tok` turns those files into one local report.

## Features

- **Multi-CLI** — one command across several coding agents
- **Local only** — no account login, no telemetry, no network required
- **Token breakdown** — input, output, cache read/write, reasoning
- **API-equivalent USD** — prefers a cost already stored in the log; otherwise uses `pricing_data.json`
- **Unknown models stay visible** — tokens are counted; USD is marked unpriced instead of inventing a rate
- **Views** — by tool, model, day (Asia/Shanghai), project, and session
- **Date / tool filters** — `-S/--since`, `-U/--until`, `-t/--tool`
- **JSON output** — `-j/--json` for scripts
- **Rich tables** when the optional `rich` package is installed; plain text otherwise

## Supported CLIs

| CLI | `tool` id | Typical local data (see `discover`) |
| --- | --- | --- |
| Claude Code | `claude` | `~/.config/claude/` or `~/.claude/` |
| OpenAI Codex CLI | `codex` | `~/.codex/` |
| Grok CLI | `grok` | `~/.grok/` |
| Gemini CLI | `gemini` | `~/.gemini/` |
| Aider | `aider` | `~/.aider/` and project `.aider*` files |
| OpenCode | `opencode` | `~/.local/share/opencode/` or `~/.opencode/` |
| Amp | `amp` | `~/.amp/` |
| GitHub Copilot CLI | `copilot` | `~/.copilot/` |

Exact paths differ by version and OS. Run `tok w` on your machine. If `docs/SOURCES.md` is present, it has parser-level notes.

## Install

Works on Windows, macOS, and Linux. Python 3.11 or newer. Windows needs the `tzdata` package for Asia/Shanghai day boundaries; it is installed automatically with `tok`.

```bash
git clone https://github.com/652036/tok.git
cd tok
pip install -e ".[dev]"
tok
```

Optional nicer tables:

```bash
pip install rich
```

A `pyproject.toml` in this repo defines the `tok` console script (deprecated alias: `ai-usage`). After install:

```bash
tok --help
```

From a source checkout without install:

```bash
PYTHONPATH=src python -m tok --help
```

## Usage

```bash
# Totals by tool (default command)
tok

# Other views
tok m            # by model   (aliases: model, by-model)
tok d            # by day     (aliases: day, by-day)
tok p            # by project (aliases: proj, project, by-project)
tok s            # sessions   (aliases: sess, session, sessions)
tok w            # discover   (aliases: where, discover)

# Date range — interpreted as Asia/Shanghai midnight
# -U/--until is inclusive of that calendar day
tok -S 2026-08-01
tok -S 2026-08-01 -U 2026-08-18

# Single tool (comma-separated for several)
tok m -t claude
tok -t claude,codex

# Machine-readable
tok d -j -S 2026-08-01

# Data lives under a different home directory (--home works before or after)
tok --home /path/to/home
tok m -H /path/to/home
tok w -H /path/to/home

# Which local directories exist?
tok w
```

Exit status is `0` when the run succeeds, including the case where no local data is found (a short hint is printed).

### Columns

Tables include some or all of: tool, model, project, day, session, days, sessions, input, output, cache read, cache write, reasoning, total tokens, API USD.

A **TOTAL** row is always appended. Token counts use thousands separators (`1,234,567`). Money is shown as `$1,234.5678` (4 decimal places, 5–6 for sub-dollar amounts).

If some events could not be priced, a note reports how many **unpriced tokens** were counted.

## How it works

Everything stays on the local filesystem.

1. **Discover** well-known data directories under your home (or `-H/--home`).
2. **Parse** usage metadata only — timestamps, model names, token counters, optional recorded cost, project/session ids. Parsers do not read prompt or completion text, and they never print API keys.
3. **Price** each event: use the log's own cost when present; otherwise look up public list prices in `src/tok/pricing_data.json`.
4. **Aggregate** and print a table or JSON.

No cloud account is contacted. You can run this offline.

## Pricing disclaimer

- Figures are **API list-price estimates in USD**, not invoices and not your subscription bill.
- A cost already stored in a log (`raw_cost_usd`) wins over the price table.
- Otherwise rates come from `pricing_data.json`. That file is a snapshot of the same LiteLLM-style table [sub2API](https://github.com/Wei-Shaw/sub2api) uses ([Wei-Shaw/model-price-repo](https://github.com/Wei-Shaw/model-price-repo)), **not** a live official vendor API. It will go stale; prices change. Refresh by updating `docs/vendor/model_prices_and_context_window.json` and re-running `scripts/import_litellm_prices.py` (see `docs/PRICING_SOURCES.md`).
- If a model is missing from the table, tokens are still counted and USD is left **unpriced**. This project will not invent official prices.
- Cache and reasoning tokens are priced only when the table has a matching rate.
- Flat-rate / included-quota plans are not modeled. Treat the USD column as “what this would have cost at API list prices,” not “what I paid.”
- Not affiliated with Anthropic, OpenAI, Google, xAI, Sourcegraph, GitHub, or any other vendor.

## Privacy

- **Read-only.** The tool does not modify your CLI logs.
- **Usage fields only.** Token counts, model names, timestamps, project/session identifiers, file paths of the logs themselves.
- **No prompt contents.** Parsers skip message bodies and file attachments.
- **No API keys or credentials** are read or printed.
- **No upload.** There is no telemetry, crash reporter, or network client for your data.
- JSON output is written to stdout on your machine; you decide whether to save or share it.

## Configuration

No config file is required.

| Flag / environment | Meaning |
| --- | --- |
| `-H` / `--home PATH` | Treat `PATH` as the home directory when locating CLI data |
| `TOK_HOME` | Same as `--home` when the flag is omitted (preferred) |
| `AI_USAGE_HOME` | Deprecated alias for `TOK_HOME` |
| `-S` / `--since YYYY-MM-DD` | Include events on/after that Asia/Shanghai calendar day |
| `-U` / `--until YYYY-MM-DD` | Include events on/before that Asia/Shanghai calendar day |
| `-t` / `--tool NAME` | Restrict to one or more tool ids |
| `-j` / `--json` | JSON instead of a table |
| `TOK_DEBUG=1` | Print a traceback on unexpected errors |

The default timezone for `-S` / `--since` and `-U` / `--until` and for the **by-day** view is **Asia/Shanghai**. Event timestamps are stored as timezone-aware UTC internally.

## Development

```bash
git clone https://github.com/652036/tok.git
cd tok
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check
pytest -q
```

Shared types and field names live in [DESIGN.md](DESIGN.md). Do not rename `UsageEvent` / `CostBreakdown` fields without updating every parser.

Suggested layout:

- `src/tok/models.py` — `UsageEvent`, `CostBreakdown`
- `src/tok/pricing.py` + `pricing_data.json` — `price_event()`
- `src/tok/parsers/` — one module per CLI, each exposing `parse(root=None) -> list[UsageEvent]`
- `src/tok/cli.py` / `report.py` — this CLI and tables
- `docs/SOURCES.md` — where each parser looks on disk (when present)

## License

[MIT](LICENSE). Copyright (c) tok contributors.

## Contributing

Issues and pull requests are welcome.

1. Keep parsers **read-only** and limited to usage metadata (no prompts, no API keys).
2. Do not invent official prices. Add a model to `pricing_data.json` with a public source, or leave it unpriced.
3. Add or update tests for new commands and parsers.
4. Follow the field names in [DESIGN.md](DESIGN.md).

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, parser guidelines, and the privacy contract.
