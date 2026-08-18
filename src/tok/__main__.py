"""Allow `python -m tok`."""

from __future__ import annotations

from tok.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
