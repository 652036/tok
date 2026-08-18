"""Tests for grok / gemini / aider / opencode / amp parsers.

Samples under ``sample_data/`` are synthetic and contain no prompts,
completions, or credentials. These tests also assert that events stay
public-safe (no message bodies, no API keys).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tok.parsers.aider import parse as parse_aider  # noqa: E402
from tok.parsers.amp import parse as parse_amp  # noqa: E402
from tok.parsers.gemini import parse as parse_gemini  # noqa: E402
from tok.parsers.grok import parse as parse_grok  # noqa: E402
from tok.parsers.opencode import parse as parse_opencode  # noqa: E402

SAMPLE = ROOT / "sample_data"

# Small metadata flags parsers may attach. Never content or secrets.
ALLOWED_EXTRA_KEYS = {
    "format",
    "session_update",
    "tool_tokens",
    "credits",
    "level",
}
FORBIDDEN_EXTRA_KEYS = {
    "content",
    "message",
    "text",
    "prompt",
    "api_key",
    "authorization",
    "cookies",
    "cookie",
    "access_token",
    "refresh_token",
}


def _assert_public_safe(events, tool: str) -> None:
    assert events, f"{tool}: expected at least one event"
    for event in events:
        assert event.tool == tool
        assert event.timestamp.tzinfo is not None
        assert event.timestamp.utcoffset() == timezone.utc.utcoffset(event.timestamp)
        assert not (FORBIDDEN_EXTRA_KEYS & set(event.extra))
        assert set(event.extra).issubset(ALLOWED_EXTRA_KEYS)
        blob = json.dumps(event.extra, default=str) + event.model + (event.project or "")
        assert "sk-" not in blob
        assert "Bearer" not in blob
        assert "xai-" not in blob
        assert "AIza" not in blob
        # No leaked prose from the synthetic redacted markers either.
        assert "(redacted)" not in blob
        assert "Hello" not in (event.model or "")


def test_missing_root_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert parse_grok(missing) == []
    assert parse_gemini(missing) == []
    assert parse_aider(missing) == []
    assert parse_opencode(missing) == []
    assert parse_amp(missing) == []


def test_empty_dir_returns_empty(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert parse_grok(empty) == []
    assert parse_gemini(empty) == []
    assert parse_aider(empty) == []
    assert parse_opencode(empty) == []
    assert parse_amp(empty) == []


def test_unreadable_and_malformed_files_are_skipped(tmp_path: Path) -> None:
    junk = tmp_path / "logs"
    junk.mkdir()
    (junk / "broken.json").write_text("{not json", encoding="utf-8")
    (junk / "empty.jsonl").write_text("\n\n", encoding="utf-8")
    (junk / "auth.json").write_text(
        '{"api_key": "sk-secret-should-never-be-read"}',
        encoding="utf-8",
    )
    good = {
        "model": "grok-4",
        "timestamp": "2026-08-01T00:00:00Z",
        "input_tokens": 3,
        "output_tokens": 1,
    }
    (junk / "ok.json").write_text(json.dumps(good), encoding="utf-8")
    events = parse_grok(junk)
    assert len(events) == 1
    assert events[0].input_tokens == 3
    blob = json.dumps([e.extra for e in events])
    assert "sk-secret" not in blob


# ---------------------------------------------------------------------------
# Grok
# ---------------------------------------------------------------------------


def test_grok_parses_turn_completed_and_aliases() -> None:
    events = parse_grok(SAMPLE / "grok")
    _assert_public_safe(events, "grok")
    # Two turn_completed rows + one assumed-alias usage.json
    assert len(events) >= 2

    first = next(e for e in events if e.input_tokens == 1000)
    assert first.model == "grok-4.5-build"
    assert first.output_tokens == 340
    assert first.cache_read_tokens == 200
    assert first.cache_write_tokens == 50
    assert first.reasoning_tokens == 80
    assert first.raw_cost_usd == pytest.approx(1.5)
    assert first.session_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert first.project == "proj"
    assert first.timestamp.year == 2026
    assert first.timestamp.month == 8

    nested = next(e for e in events if e.input_tokens == 800)
    assert nested.output_tokens == 120
    assert nested.cache_read_tokens == 100
    assert nested.reasoning_tokens == 20
    assert nested.raw_cost_usd == pytest.approx(0.4)

    alias = next(e for e in events if e.model == "grok-4")
    assert alias.input_tokens == 50
    assert alias.output_tokens == 10


def test_grok_default_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    dest = (
        home
        / ".grok"
        / "sessions"
        / "cwd"
        / "sess-1"
        / "updates.jsonl"
    )
    dest.parent.mkdir(parents=True)
    dest.write_text(
        json.dumps(
            {
                "sessionUpdate": "turn_completed",
                "timestamp": "2026-08-10T00:00:00Z",
                "usage": {"inputTokens": 10, "outputTokens": 2},
                "modelUsage": {"grok-4.5-build": True},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_USAGE_HOME", str(home))
    monkeypatch.delenv("GROK_HOME", raising=False)
    events = parse_grok()
    assert len(events) == 1
    assert events[0].input_tokens == 10
    assert events[0].model == "grok-4.5-build"


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


def test_gemini_parses_jsonl_and_json_sessions() -> None:
    events = parse_gemini(SAMPLE / "gemini")
    _assert_public_safe(events, "gemini")
    assert len(events) == 2

    pro = next(e for e in events if e.model == "gemini-2.5-pro")
    # input 120 includes cached 20
    assert pro.input_tokens == 100
    assert pro.output_tokens == 30
    assert pro.cache_read_tokens == 20
    assert pro.reasoning_tokens == 5
    assert pro.extra.get("tool_tokens") == 2
    assert pro.session_id == "90a6c51d-c8dd-480c-a6a4-30b0265bb001"
    assert pro.project == "abc123def"
    assert pro.timestamp.year == 2026
    assert pro.timestamp.month == 5

    flash = next(e for e in events if e.model == "gemini-2.5-flash")
    assert flash.input_tokens == 80
    assert flash.output_tokens == 15
    assert flash.reasoning_tokens == 3
    assert flash.session_id == "11111111-2222-3333-4444-555555555555"


def test_gemini_default_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    dest = home / ".gemini" / "tmp" / "hash1" / "chats" / "session-abc.jsonl"
    dest.parent.mkdir(parents=True)
    dest.write_text(
        json.dumps({"sessionId": "abc", "projectHash": "hash1", "startTime": "2026-01-01T00:00:00Z"})
        + "\n"
        + json.dumps(
            {
                "type": "gemini",
                "timestamp": "2026-01-01T00:00:01Z",
                "model": "gemini-2.5-pro",
                "tokens": {"input": 8, "output": 2, "cached": 0, "total": 10},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_USAGE_HOME", str(home))
    events = parse_gemini()
    assert len(events) == 1
    assert events[0].input_tokens == 8


# ---------------------------------------------------------------------------
# Aider
# ---------------------------------------------------------------------------


def test_aider_parses_history_token_lines_and_json() -> None:
    events = parse_aider(SAMPLE / "aider")
    _assert_public_safe(events, "aider")
    # 4 markdown token lines + 1 jsonl sidecar
    assert len(events) == 5

    by_in = {e.input_tokens: e for e in events}
    first = by_in[12_000]
    assert first.output_tokens == 133
    assert first.raw_cost_usd == pytest.approx(0.04)
    assert first.model == "openai/gpt-4o"
    assert first.timestamp.year == 2026
    assert first.timestamp.month == 3

    cached = by_in[216_000]
    assert cached.cache_write_tokens == 108_000
    assert cached.output_tokens == 1_400
    assert cached.raw_cost_usd == pytest.approx(0.75)

    comma = by_in[11_740]
    assert comma.output_tokens == 2_012

    kform = by_in[2_700]
    assert kform.output_tokens == 73
    assert kform.raw_cost_usd == pytest.approx(0.0091)

    sidecar = next(e for e in events if e.model == "anthropic/claude-sonnet-4")
    assert sidecar.input_tokens == 500
    assert sidecar.output_tokens == 80
    assert sidecar.raw_cost_usd == pytest.approx(0.012)

    # History file contains "# USER" / "(redacted)" — must not leak.
    dumped = json.dumps([e.__dict__ for e in events], default=str)
    assert "(redacted)" not in dumped
    assert "# USER" not in dumped


def test_aider_history_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hist = tmp_path / "custom.history.md"
    hist.write_text(
        "Main model: gpt-4o\n"
        "Tokens: 100 sent, 20 received. Cost: $0.01 message, $0.01 session.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_USAGE_HOME", str(tmp_path / "empty-home"))
    (tmp_path / "empty-home").mkdir()
    monkeypatch.setenv("AIDER_HISTORY", str(hist))
    events = parse_aider()
    assert len(events) == 1
    assert events[0].input_tokens == 100
    assert events[0].output_tokens == 20
    assert events[0].raw_cost_usd == pytest.approx(0.01)


def test_aider_default_does_not_scan_home_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stray .aider.chat.history.md deep under home must be ignored."""
    home = tmp_path / "home"
    buried = home / "projects" / "huge" / ".aider.chat.history.md"
    buried.parent.mkdir(parents=True)
    buried.write_text("Tokens: 999 sent, 1 received.\n", encoding="utf-8")
    monkeypatch.setenv("AI_USAGE_HOME", str(home))
    monkeypatch.delenv("AIDER_HISTORY", raising=False)
    assert parse_aider() == []


def test_aider_given_root_searches_history_names(tmp_path: Path) -> None:
    project = tmp_path / "my-repo"
    project.mkdir()
    (project / ".aider.chat.history.md").write_text(
        "Main model: gpt-4o\nTokens: 40 sent, 5 received.\n",
        encoding="utf-8",
    )
    events = parse_aider(project)
    assert len(events) == 1
    assert events[0].input_tokens == 40


# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------


def test_opencode_parses_message_json_and_skips_session_rollup() -> None:
    events = parse_opencode(SAMPLE / "opencode")
    _assert_public_safe(events, "opencode")
    # message + legacy wrapper; session rollup for ses_01synthetic dropped
    sessions = {e.session_id for e in events}
    assert "ses_01synthetic" in sessions
    assert "ses_02legacy" in sessions
    # Only one event for the session that also has a session JSON rollup
    primary = [e for e in events if e.session_id == "ses_01synthetic"]
    assert len(primary) == 1
    ev = primary[0]
    assert ev.model in {"anthropic/claude-sonnet-4", "claude-sonnet-4"}
    assert ev.input_tokens == 400
    assert ev.output_tokens == 90
    assert ev.reasoning_tokens == 12
    assert ev.cache_read_tokens == 40
    assert ev.cache_write_tokens == 8
    assert ev.raw_cost_usd == pytest.approx(0.021)

    legacy = next(e for e in events if e.session_id == "ses_02legacy")
    assert legacy.input_tokens == 100
    assert legacy.output_tokens == 20
    assert legacy.raw_cost_usd == pytest.approx(0.005)


def test_opencode_sqlite(tmp_path: Path) -> None:
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE session (
            id TEXT,
            project_id TEXT,
            directory TEXT,
            model TEXT,
            cost REAL,
            tokens_input INTEGER,
            tokens_output INTEGER,
            tokens_reasoning INTEGER,
            tokens_cache_read INTEGER,
            tokens_cache_write INTEGER,
            time_created INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO session VALUES (
            'ses_sql', 'proj', '/tmp/demo',
            '{"id":"gpt-4.1","providerID":"openai"}',
            0.03, 70, 9, 4, 11, 2, 1770200000000
        )
        """
    )
    conn.commit()
    conn.close()
    events = parse_opencode(tmp_path)
    assert len(events) == 1
    ev = events[0]
    assert ev.tool == "opencode"
    assert ev.input_tokens == 70
    assert ev.output_tokens == 9
    assert ev.reasoning_tokens == 4
    assert ev.cache_read_tokens == 11
    assert ev.cache_write_tokens == 2
    assert ev.raw_cost_usd == pytest.approx(0.03)
    assert ev.session_id == "ses_sql"
    assert ev.model in {"openai/gpt-4.1", "gpt-4.1"}


def test_opencode_default_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    dest = (
        home
        / ".local"
        / "share"
        / "opencode"
        / "storage"
        / "message"
        / "ses_x"
        / "msg_x.json"
    )
    dest.parent.mkdir(parents=True)
    dest.write_text(
        json.dumps(
            {
                "id": "msg_x",
                "role": "assistant",
                "sessionID": "ses_x",
                "modelID": "test-model",
                "tokens": {
                    "input": 6,
                    "output": 1,
                    "reasoning": 0,
                    "cache": {"read": 0, "write": 0},
                },
                "time": {"created": 1770000000000},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_USAGE_HOME", str(home))
    events = parse_opencode()
    assert len(events) == 1
    assert events[0].input_tokens == 6


# ---------------------------------------------------------------------------
# Amp
# ---------------------------------------------------------------------------


def test_amp_parses_thread_usage_aliases() -> None:
    events = parse_amp(SAMPLE / "amp")
    _assert_public_safe(events, "amp")
    # Two assistant messages; ledger is a fallback and must not double-count
    assert len(events) == 2
    first = next(e for e in events if e.input_tokens == 100)
    assert first.output_tokens == 50
    assert first.cache_read_tokens == 200
    assert first.cache_write_tokens == 500
    assert first.model == "claude-haiku-4-5-20251001"
    assert first.session_id == "T-00000000-0000-4000-8000-000000000001"
    assert first.extra.get("credits") == pytest.approx(1.5)
    assert first.raw_cost_usd is None  # credits are not USD
    assert first.timestamp.year == 2026

    second = next(e for e in events if e.input_tokens == 40)
    assert second.output_tokens == 12
    assert second.cache_read_tokens == 10


def test_amp_ledger_fallback(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "model": "claude-haiku-4-5-20251001",
                "credits": 2.0,
                "timestamp": "2026-08-02T00:00:00Z",
                "tokens": {"input": 15, "output": 3},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    events = parse_amp(tmp_path)
    assert len(events) == 1
    assert events[0].input_tokens == 15
    assert events[0].extra.get("credits") == pytest.approx(2.0)


def test_amp_default_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    dest = home / ".amp" / "threads" / "T-abc.json"
    dest.parent.mkdir(parents=True)
    dest.write_text(
        json.dumps(
            {
                "id": "T-abc",
                "created": 1770100000000,
                "messages": [
                    {
                        "role": "assistant",
                        "usage": {
                            "model": "amp-model",
                            "inputTokens": 4,
                            "outputTokens": 1,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_USAGE_HOME", str(home))
    events = parse_amp()
    assert len(events) == 1
    assert events[0].input_tokens == 4
    assert events[0].session_id == "T-abc"


def test_load_all_events_discovers_undotted_sample_tree() -> None:
    """``parse(sample_data)`` must resolve sample_data/<tool>/ (home-style)."""
    grok = parse_grok(SAMPLE)
    gemini = parse_gemini(SAMPLE)
    aider = parse_aider(SAMPLE)
    opencode = parse_opencode(SAMPLE)
    amp = parse_amp(SAMPLE)
    assert grok and all(e.tool == "grok" for e in grok)
    assert gemini and all(e.tool == "gemini" for e in gemini)
    assert aider and all(e.tool == "aider" for e in aider)
    assert opencode and all(e.tool == "opencode" for e in opencode)
    assert amp and all(e.tool == "amp" for e in amp)
