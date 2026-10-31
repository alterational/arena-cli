"""终端 UI：颜色、键值表、表格、JSON 输出。所有命令都通过它说话。"""

from __future__ import annotations

import json
import os
import sys
import unicodedata
from typing import Any, Iterable, Sequence


def display_width(text: str) -> int:
    """中文/全角算 2 列，保证表格在终端里对齐。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))

_CODES = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m",
    "grey": "\033[90m",
}


class UI:
    """--json 时闭嘴只吐 JSON；--quiet 时只留结果；否则给人看的排版。"""

    def __init__(self, json_mode: bool = False, color: bool | None = None,
                 quiet: bool = False, verbose: bool = False) -> None:
        self.json_mode = json_mode
        self.quiet = quiet
        self.verbose = verbose
        if color is None:
            color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        self.color = bool(color)

    # ---- 基础 -------------------------------------------------------------
    def paint(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        return "".join(_CODES[s] for s in styles) + text + _CODES["reset"]

    def out(self, text: str = "") -> None:
        print(text)

    def info(self, msg: str) -> None:
        if not self.quiet and not self.json_mode:
            print(self.paint(msg, "grey"))

    def ok(self, msg: str) -> None:
        if not self.quiet and not self.json_mode:
            print(f"{self.paint('✓', 'green')} {msg}")

    def warn(self, msg: str) -> None:
        print(f"{self.paint('!', 'yellow')} {msg}", file=sys.stderr)

    def err(self, msg: str) -> None:
        print(f"{self.paint('✗', 'red')} {msg}", file=sys.stderr)

    def debug(self, msg: str) -> None:
        if self.verbose:
            print(self.paint(f"· {msg}", "grey"), file=sys.stderr)

    # ---- 结构化 -----------------------------------------------------------
    def json(self, data: Any) -> None:
        print(json.dumps(data, ensure_ascii=False, indent=2))

    def emit(self, data: Any, human: str | None = None) -> None:
        """json 模式吐 JSON，否则吐人话。"""
        if self.json_mode:
            self.json(data)
        elif human is not None:
            print(human)

    def kv(self, pairs: Sequence[tuple[str, Any]], title: str | None = None) -> None:
        if self.json_mode:
            return
        if title:
            print(self.paint(title, "bold"))
        width = max((display_width(k) for k, _ in pairs), default=0)
        for key, value in pairs:
            if value is None or value == "" or value == []:
                value = self.paint("-", "grey")
            elif isinstance(value, bool):
                value = self.paint("yes" if value else "no", "green" if value else "red")
            elif isinstance(value, (list, tuple)):
                value = ", ".join(str(v) for v in value)
            print(f"  {self.paint(pad(key, width), 'cyan')}  {value}")

    def table(self, headers: Sequence[str], rows: Iterable[Sequence[Any]],
              title: str | None = None) -> None:
        if self.json_mode:
            return
        rows = [[("" if c is None else str(c)) for c in row] for row in rows]
        if title:
            print(self.paint(title, "bold"))
        if not rows:
            print(self.paint("  (空)", "grey"))
            return
        widths = [display_width(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], display_width(cell))
        print("  " + "  ".join(self.paint(pad(h, widths[i]), "bold")
                               for i, h in enumerate(headers)).rstrip())
        for row in rows:
            print("  " + "  ".join(pad(cell, widths[i]) for i, cell in enumerate(row)).rstrip())

    # ---- 错误 -------------------------------------------------------------
    def fail(self, code: str, message: str, hint: str | None = None) -> None:
        if self.json_mode:
            self.json({"ok": False, "code": code, "error": message, "hint": hint})
        else:
            self.err(f"{code}: {message}")
            if hint:
                print(f"  {self.paint('→ ' + hint, 'grey')}", file=sys.stderr)
