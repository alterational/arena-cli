#!/usr/bin/env python3
"""向后兼容入口：老命令 `scripts/arena_agent.py <login|ask|check|...>` 仍然可用。

等价的现代写法：`arena <命令>`（见 arena/cli.py）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena.engine import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
