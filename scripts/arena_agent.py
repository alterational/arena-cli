#!/usr/bin/env python3
"""Backward-compatible entry point: the legacy command `scripts/arena_agent.py <login|ask|check|...>` still works.

Modern equivalent: `arena <command>` (see arena/cli.py).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena.engine import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
