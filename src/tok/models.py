"""Shared usage and cost dataclasses.

Field names are a public contract — do not rename them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class UsageEvent:
    tool: str  # claude | codex | grok | gemini | aider | opencode | amp | copilot
    timestamp: datetime  # timezone-aware UTC
    model: str
    project: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    raw_cost_usd: float | None = None  # if the log already recorded a cost
    source_path: str = ""
    session_id: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        """Sum of the five token fields as recorded (no inferred merging)."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.reasoning_tokens
        )


@dataclass
class CostBreakdown:
    input_usd: float = 0.0
    output_usd: float = 0.0
    cache_read_usd: float = 0.0
    cache_write_usd: float = 0.0
    reasoning_usd: float = 0.0
    total_usd: float = 0.0
    priced: bool = True  # False if model unknown
    source: str = "table"  # table | log | unknown

    def __add__(self, other: CostBreakdown) -> CostBreakdown:
        source = self.source if self.source == other.source else "mixed"
        return CostBreakdown(
            input_usd=self.input_usd + other.input_usd,
            output_usd=self.output_usd + other.output_usd,
            cache_read_usd=self.cache_read_usd + other.cache_read_usd,
            cache_write_usd=self.cache_write_usd + other.cache_write_usd,
            reasoning_usd=self.reasoning_usd + other.reasoning_usd,
            total_usd=self.total_usd + other.total_usd,
            priced=self.priced and other.priced,
            source=source,
        )

    def __iadd__(self, other: CostBreakdown) -> CostBreakdown:
        added = self + other
        self.input_usd = added.input_usd
        self.output_usd = added.output_usd
        self.cache_read_usd = added.cache_read_usd
        self.cache_write_usd = added.cache_write_usd
        self.reasoning_usd = added.reasoning_usd
        self.total_usd = added.total_usd
        self.priced = added.priced
        self.source = added.source
        return self
