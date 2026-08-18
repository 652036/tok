"""Tests for Claude Code and Codex CLI parsers using synthetic sample_data."""

from __future__ import annotations

import os
import sys
from datetime import timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_usage.parsers.claude import parse as parse_claude  # noqa: E402
from ai_usage.parsers.codex import parse as parse_codex  # noqa: E402

SAMPLE = ROOT / "sample_data"
FORBIDDEN_EXTRA_KEYS = {
    "content",
    "message",
    "text",
    "prompt",
    "api_key",
    "authorization",
    "cookies",
    "cookie",
}


def _assert_public_safe(events) -> None:
    for event in events:
        assert event.timestamp.tzinfo is not None
        assert event.timestamp.utcoffset() == timezone.utc.utcoffset(event.timestamp)
        assert set(event.extra).issubset({"cwd", "version"})
        assert not (FORBIDDEN_EXTRA_KEYS & set(event.extra))
        blob = repr(event.extra)
        assert "sk-" not in blob
        assert "Bearer" not in blob
        assert "content" not in event.extra


def test_claude_parses_sample_usage_records() -> None:
    events = parse_claude(SAMPLE / "claude")
    assert len(events) == 4
    assert all(e.tool == "claude" for e in events)
    _assert_public_safe(events)

    by_model = {e.model: e for e in events}
    sonnet_events = [e for e in events if e.model == "claude-sonnet-4-20250514"]
    assert len(sonnet_events) == 2

    first = sonnet_events[0]
    assert first.input_tokens == 100
    assert first.output_tokens == 50
    assert first.cache_read_tokens == 1000
    assert first.cache_write_tokens == 200
    assert first.project == "/home/user/demo-app"
    assert first.session_id == "11111111-1111-1111-1111-111111111111"
    assert first.extra.get("cwd") == "/home/user/demo-app"
    assert first.extra.get("version") == "2.1.0"
    assert first.raw_cost_usd == pytest.approx(0.012)

    second = sonnet_events[1]
    assert second.input_tokens == 10
    assert second.output_tokens == 20
    assert second.cache_read_tokens == 0
    assert second.cache_write_tokens == 0

    url_event = by_model["claude-opus-4-20250514"]
    assert url_event.project == "/tmp/urlproj"
    assert url_event.input_tokens == 40
    assert url_event.output_tokens == 10
    assert url_event.timestamp.tzinfo is not None
    assert url_event.timestamp.year == 2026
    assert url_event.timestamp.month == 4
    assert url_event.timestamp.day == 1
    assert url_event.timestamp.hour == 8

    stats_event = by_model["claude-haiku-4-20250514"]
    assert stats_event.input_tokens == 7
    assert stats_event.output_tokens == 3
    assert "stats" in stats_event.source_path.replace("\\", "/")


def test_claude_skips_records_without_usage() -> None:
    events = parse_claude(SAMPLE / "claude")
    # 6 JSON objects in the main session file, only 2 have usage.
    session_events = [
        e
        for e in events
        if e.session_id == "11111111-1111-1111-1111-111111111111"
    ]
    assert len(session_events) == 2


def test_claude_honors_ai_usage_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    dest = home / ".claude" / "projects" / "-tmp-isolated" / "sess.jsonl"
    dest.parent.mkdir(parents=True)
    dest.write_text(
        '{"type":"assistant","timestamp":"2026-05-01T00:00:00Z",'
        '"cwd":"/tmp/isolated","message":{"model":"claude-test",'
        '"usage":{"input_tokens":3,"output_tokens":1}}}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_USAGE_HOME", str(home))
    events = parse_claude()
    assert len(events) == 1
    assert events[0].project == "/tmp/isolated"
    assert events[0].input_tokens == 3


def test_codex_parses_rollout_and_alternate_layouts() -> None:
    events = parse_codex(SAMPLE / "codex")
    assert len(events) == 4
    assert all(e.tool == "codex" for e in events)
    _assert_public_safe(events)

    rollout = [e for e in events if e.model == "gpt-5.4"]
    assert len(rollout) == 2
    first, second = rollout
    # Cached tokens are billed separately; OpenAI includes them in input_tokens.
    assert first.input_tokens == 18193 - 10624
    assert first.cache_read_tokens == 10624
    assert first.output_tokens == 371
    assert first.reasoning_tokens == 38
    assert first.cache_write_tokens == 0
    assert first.project == "/home/user/demo-app"
    assert first.session_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert first.extra.get("cwd") == "/home/user/demo-app"
    assert first.extra.get("version") == "0.135.0"

    # Second turn uses the cumulative delta (duplicate snapshot is dropped).
    assert second.input_tokens == 2000 - 500
    assert second.cache_read_tokens == 500
    assert second.output_tokens == 100
    assert second.reasoning_tokens == 20

    completed = next(e for e in events if e.model == "gpt-5")
    assert completed.input_tokens == 80 - 20
    assert completed.cache_read_tokens == 20
    assert completed.output_tokens == 15
    assert completed.reasoning_tokens == 5

    alt = next(e for e in events if e.model == "gpt-5.3-codex")
    assert alt.input_tokens == 30
    assert alt.output_tokens == 10
    assert alt.project == "/tmp/alt"


def test_codex_skips_unknown_history_and_secret_files() -> None:
    events = parse_codex(SAMPLE / "codex")
    sources = {Path(e.source_path).name for e in events}
    assert "history.jsonl" not in sources
    assert "junk.json" not in sources
    assert "secrets.json" not in sources
    for event in events:
        assert "sk-dummy" not in repr(event)
        assert "Bearer dummy" not in repr(event)


def test_codex_does_not_store_message_content(tmp_path: Path) -> None:
    path = tmp_path / "rollout-leak-check.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"timestamp":"2026-06-01T00:00:00Z","type":"session_meta","payload":{"id":"leak-sess","cwd":"/tmp/x","cli_version":"0.1.0"}}',
                '{"timestamp":"2026-06-01T00:00:01Z","type":"event_msg","payload":{"type":"user_message","message":"LEAKED_PROMPT"}}',
                '{"timestamp":"2026-06-01T00:00:02Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":9,"cached_input_tokens":0,"output_tokens":2,"reasoning_output_tokens":0}}}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    events = parse_codex(path)
    assert len(events) == 1
    assert events[0].input_tokens == 9
    dumped = repr(events[0].extra)
    assert "LEAKED_PROMPT" not in dumped
    assert "message" not in events[0].extra


def test_missing_root_returns_empty() -> None:
    assert parse_claude(Path("/tmp/ai-usage-missing-claude-root")) == []
    assert parse_codex(Path("/tmp/ai-usage-missing-codex-root")) == []

def test_parse_accepts_home_containing_dotted_tool_dirs(tmp_path: Path) -> None:
    """load_all_events passes a home; parsers must look in .claude / .codex."""
    claude = tmp_path / ".claude" / "projects" / "-tmp-home" / "s.jsonl"
    claude.parent.mkdir(parents=True)
    claude.write_text(
        '{"type":"assistant","timestamp":"2026-05-02T00:00:00Z",'
        '"cwd":"/tmp/home-layout","message":{"model":"claude-test",'
        '"usage":{"input_tokens":4,"output_tokens":2}}}\n',
        encoding="utf-8",
    )
    codex = tmp_path / ".codex" / "sessions" / "rollout.jsonl"
    codex.parent.mkdir(parents=True)
    codex.write_text(
        '{"timestamp":"2026-05-02T00:00:00Z","type":"event_msg",'
        '"payload":{"type":"token_count","info":{"last_token_usage":'
        '{"input_tokens":8,"cached_input_tokens":0,"output_tokens":1,'
        '"reasoning_output_tokens":0}}}}\n',
        encoding="utf-8",
    )
    claude_events = parse_claude(tmp_path)
    codex_events = parse_codex(tmp_path)
    assert len(claude_events) == 1
    assert claude_events[0].input_tokens == 4
    assert claude_events[0].project == "/tmp/home-layout"
    assert len(codex_events) == 1
    assert codex_events[0].input_tokens == 8

