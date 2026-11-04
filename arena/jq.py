"""Minimal jq: just enough for our needs, with no external dependencies.

Supported:
    .                      the whole result
    .answer                get a field
    .data.chats[].title    get a field after expanding an array (multiple results are printed one per line)
    .data.chats[0]         index
    .items[].name | .[0]   pipes
    .field //              (arithmetic and conditionals are not supported; value lookup only)

Output rules are close to jq: strings are printed as-is; objects/arrays are printed as JSON (multiple results one per line).
"""

from __future__ import annotations

import json
import re
from typing import Any

_TOKEN = re.compile(r"\.([A-Za-z_][\w-]*)|\[\s*(\d*)\s*\]")


def _walk(value: Any, expr: str) -> list[Any]:
    """Apply an expression to value and return a list of results ([] expands into multiple)."""
    results: list[Any] = [value]
    for match in _TOKEN.finditer(expr):
        name, index = match.group(1), match.group(2)
        next_results: list[Any] = []
        for item in results:
            if name is not None:
                if isinstance(item, dict) and name in item:
                    next_results.append(item[name])
            elif index == "":                       # []
                if isinstance(item, list):
                    next_results.extend(item)
                elif isinstance(item, dict):
                    next_results.extend(item.values())
            else:                                    # [n]
                if isinstance(item, list):
                    i = int(index)
                    if -len(item) <= i < len(item):
                        next_results.append(item[i])
        results = next_results
    return results


def evaluate(expr: str, data: Any) -> list[Any]:
    stages = [s.strip() for s in expr.split("|")]
    current: list[Any] = [data]
    for stage in stages:
        out: list[Any] = []
        for item in current:
            out.extend(_walk(item, stage))
        current = out
    return current


def render(expr: str, data: Any) -> str:
    """Return text suitable for direct printing."""
    values = evaluate(expr, data)
    if not values:
        return ""
    lines = []
    for v in values:
        if isinstance(v, str):
            lines.append(v)
        elif v is None:
            lines.append("null")
        else:
            lines.append(json.dumps(v, ensure_ascii=False))
    return "\n".join(lines)
