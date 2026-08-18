"""Smoke tests for report rendering.

Constructs a few in-memory UsageEvent objects and checks that every
print_* function returns without raising. Skips if models is not importable
(other agents own that module).
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from ai_usage.models import UsageEvent

    HAS_MODELS = True
    MODELS_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # ImportError or incomplete package
    HAS_MODELS = False
    MODELS_IMPORT_ERROR = exc
    UsageEvent = None  # type: ignore[misc, assignment]


from ai_usage.report import (  # noqa: E402
    format_money,
    format_tokens,
    print_by_day,
    print_by_model,
    print_by_project,
    print_discover,
    print_sessions,
    print_summary,
)


def _event(**kwargs):
    defaults = dict(
        tool="claude",
        timestamp=datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc),
        model="claude-sonnet-4",
        project="demo",
        input_tokens=1_000,
        output_tokens=200,
        cache_read_tokens=50,
        cache_write_tokens=10,
        reasoning_tokens=0,
        raw_cost_usd=0.0123,
        source_path="/tmp/demo.jsonl",
        session_id="sess-1",
    )
    defaults.update(kwargs)
    return UsageEvent(**defaults)


@unittest.skipUnless(HAS_MODELS, f"models not importable: {MODELS_IMPORT_ERROR}")
class ReportSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = [
            _event(),
            _event(
                tool="codex",
                timestamp=datetime(2026, 8, 2, 10, 0, tzinfo=timezone.utc),
                model="gpt-5",
                project="other",
                input_tokens=2_500,
                output_tokens=800,
                cache_read_tokens=0,
                cache_write_tokens=0,
                reasoning_tokens=120,
                raw_cost_usd=None,
                session_id="sess-2",
            ),
            _event(
                tool="claude",
                timestamp=datetime(2026, 8, 1, 8, 30, tzinfo=timezone.utc),
                model="claude-opus-4",
                project="demo",
                input_tokens=50_000,
                output_tokens=1_200,
                cache_read_tokens=10_000,
                cache_write_tokens=500,
                reasoning_tokens=2_000,
                raw_cost_usd=1.23456789,
                session_id="sess-3",
            ),
        ]

    def _run(self, fn, **kwargs) -> str:
        buf = StringIO()
        fn(self.events, file=buf, **kwargs)
        return buf.getvalue()

    def test_print_summary_plain(self) -> None:
        text = self._run(print_summary)
        self.assertIn("claude", text)
        self.assertIn("codex", text)
        self.assertIn("TOTAL", text)
        self.assertIn("1,000", text)
        self.assertIn("$", text)

    def test_print_by_model(self) -> None:
        text = self._run(print_by_model)
        self.assertIn("claude-sonnet-4", text)
        self.assertIn("gpt-5", text)
        self.assertIn("TOTAL", text)

    def test_print_by_day(self) -> None:
        text = self._run(print_by_day)
        # 2026-08-01 04:00 UTC = 2026-08-01 12:00 Asia/Shanghai
        self.assertIn("2026-08-01", text)
        self.assertIn("TOTAL", text)

    def test_print_by_project(self) -> None:
        text = self._run(print_by_project)
        self.assertIn("demo", text)
        self.assertIn("other", text)

    def test_print_sessions(self) -> None:
        text = self._run(print_sessions)
        self.assertIn("sess-1", text)
        self.assertIn("sess-2", text)

    def test_print_summary_json(self) -> None:
        text = self._run(print_summary, json_output=True)
        payload = json.loads(text)
        self.assertIn("rows", payload)
        self.assertIn("totals", payload)
        self.assertGreaterEqual(len(payload["rows"]), 1)
        self.assertIn("api_usd", payload["totals"])
        self.assertIn("total_tokens", payload["totals"])

    def test_print_empty_does_not_crash(self) -> None:
        buf = StringIO()
        print_summary([], file=buf)
        self.assertTrue(buf.getvalue())
        buf = StringIO()
        print_summary([], file=buf, json_output=True)
        json.loads(buf.getvalue())

    def test_print_discover(self) -> None:
        buf = StringIO()
        print_discover(
            [
                {
                    "tool": "claude",
                    "path": "~/.claude",
                    "found": True,
                    "notes": "ok",
                },
                {
                    "tool": "codex",
                    "path": "~/.codex",
                    "found": False,
                    "notes": "not found",
                },
            ],
            file=buf,
        )
        text = buf.getvalue()
        self.assertIn("claude", text)
        self.assertIn("codex", text)
        buf = StringIO()
        print_discover([], file=buf, json_output=True)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["entries"], [])


class FormatHelperTests(unittest.TestCase):
    def test_format_tokens(self) -> None:
        self.assertEqual(format_tokens(1234567), "1,234,567")
        self.assertEqual(format_tokens(0), "0")
        self.assertEqual(format_tokens(None), "0")

    def test_format_money(self) -> None:
        self.assertEqual(format_money(1234.5678), "$1,234.5678")
        self.assertEqual(format_money(0), "$0.0000")
        small = format_money(0.00012)
        self.assertTrue(small.startswith("$0.000"))
        self.assertIn("000120", small.replace(".", "").replace("0", "0") or small)


class CliHelpTests(unittest.TestCase):
    def test_build_parser_help(self) -> None:
        from ai_usage.cli import build_parser, main

        parser = build_parser()
        help_text = parser.format_help()
        self.assertIn("tok", help_text)
        self.assertIn("summary", help_text)
        self.assertIn("--since", help_text)
        self.assertIn("--json", help_text)
        self.assertIn("-H", help_text)
        self.assertIn("-t", help_text)
        self.assertIn("-j", help_text)
        self.assertIn("-S", help_text)
        self.assertIn("-U", help_text)
        for short in ("m", "d", "p", "s", "w"):
            self.assertIn(short, help_text)
        self.assertEqual(main(["--help"]), 0)

    def test_no_data_exits_zero(self) -> None:
        from ai_usage.cli import main

        code = main(["-H", "/tmp/ai-usage-no-such-home-dir"])
        self.assertEqual(code, 0)

    def test_discover_exits_zero(self) -> None:
        from ai_usage.cli import main

        code = main(["w", "-H", "/tmp/ai-usage-no-such-home-dir"])
        self.assertEqual(code, 0)

    def test_legacy_aliases(self) -> None:
        from ai_usage.cli import _CANONICAL_COMMAND, build_parser

        parser = build_parser()
        cases = (
            ("m", "by-model"),
            ("model", "by-model"),
            ("by-model", "by-model"),
            ("discover", "discover"),
            ("sessions", "sessions"),
            ("by-project", "by-project"),
            ("by-day", "by-day"),
        )
        for alias, canonical in cases:
            args = parser.parse_args([alias])
            self.assertEqual(
                _CANONICAL_COMMAND.get(args.command, args.command),
                canonical,
                msg=alias,
            )

    def test_home_before_and_after_subcommand(self) -> None:
        from ai_usage.cli import build_parser

        parser = build_parser()
        before = parser.parse_args(["--home", "/tmp/foo", "m"])
        self.assertEqual(Path(before.home), Path("/tmp/foo"))
        after = parser.parse_args(["m", "-H", "/tmp/bar"])
        self.assertEqual(Path(after.home), Path("/tmp/bar"))
        # Parent-only flag must survive when no subcommand is given
        bare = parser.parse_args(["--home", "/tmp/baz"])
        self.assertEqual(Path(bare.home), Path("/tmp/baz"))
        self.assertIsNone(bare.command)



class DuckTypedReportSmokeTests(unittest.TestCase):
    """Report functions must not require models.py — duck-typed events suffice."""

    def setUp(self) -> None:
        from types import SimpleNamespace

        self.events = [
            SimpleNamespace(
                tool="claude",
                timestamp=datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc),
                model="claude-sonnet-4",
                project="demo",
                input_tokens=1000,
                output_tokens=200,
                cache_read_tokens=50,
                cache_write_tokens=10,
                reasoning_tokens=0,
                raw_cost_usd=0.0123,
                source_path="/tmp/demo.jsonl",
                session_id="sess-1",
            ),
            SimpleNamespace(
                tool="grok",
                timestamp=datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc),
                model="grok-3",
                project=None,
                input_tokens=9_999,
                output_tokens=1,
                cache_read_tokens=0,
                cache_write_tokens=0,
                reasoning_tokens=0,
                raw_cost_usd=None,
                source_path="",
                session_id=None,
            ),
        ]

    def test_all_printers(self) -> None:
        for fn in (
            print_summary,
            print_by_model,
            print_by_day,
            print_by_project,
            print_sessions,
        ):
            buf = StringIO()
            fn(self.events, file=buf)
            self.assertTrue(buf.getvalue(), msg=fn.__name__)
            buf = StringIO()
            fn(self.events, file=buf, json_output=True)
            payload = json.loads(buf.getvalue())
            self.assertIn("rows", payload)
            self.assertIn("totals", payload)

if __name__ == "__main__":
    unittest.main()
