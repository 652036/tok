# tok — local multi-CLI token/cost stats

Python 3.11+ CLI. Package: `tok`. Entry: `tok` (deprecated alias: `ai-usage`).

## Shared contract (DO NOT CHANGE field names)

`src/tok/models.py` must export:

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class UsageEvent:
    tool: str                    # claude | codex | grok | gemini | aider | opencode | amp | copilot
    timestamp: datetime          # timezone-aware UTC
    model: str
    project: Optional[str]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    raw_cost_usd: Optional[float] = None  # if the log already recorded a cost
    source_path: str = ""
    session_id: Optional[str] = None
    extra: dict = field(default_factory=dict)

@dataclass
class CostBreakdown:
    input_usd: float = 0.0
    output_usd: float = 0.0
    cache_read_usd: float = 0.0
    cache_write_usd: float = 0.0
    reasoning_usd: float = 0.0
    total_usd: float = 0.0
    priced: bool = True          # False if model unknown
    source: str = "table"        # table | log | unknown
```

Each parser: `parse(root: Path | None = None) -> list[UsageEvent]`
Discover default roots; also accept override path.
Never read/print API keys or message content. Usage metadata only.

Pricing: `src/tok/pricing.py` + `src/tok/pricing_data.json`
`price_event(event) -> CostBreakdown` — prefer raw_cost_usd if present, else table.

CLI (`src/tok/cli.py`) commands:
- `tok` / `tok summary` — totals by tool
- `tok m` (aliases: model, by-model)
- `tok d` (aliases: day, by-day)
- `tok p` (aliases: proj, project, by-project)
- `tok s` (aliases: sess, session, sessions)
- `tok w` (aliases: where, discover) — show found data dirs
Flags: `-S/--since`, `-U/--until`, `-t/--tool`, `-j/--json`, `-H/--home`
`--home` works before or after the subcommand.

Do not invent official prices. If a model is missing, mark priced=False and still count tokens.

File ownership (avoid conflicts):
- models.py, discover.py, pricing.py, pricing_data.json, pyproject.toml — core agent
- parsers/claude.py, parsers/codex.py — parser-a
- parsers/grok.py, parsers/gemini.py, parsers/aider.py, parsers/opencode.py, parsers/amp.py — parser-b
- cli.py, report.py, README.md, tests — cli agent

## Open source

MIT license (`LICENSE`). Community docs: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`.
CI runs ruff + pytest on Python 3.11/3.12. Do not commit real usage logs or secrets.
