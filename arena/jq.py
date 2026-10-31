"""极简 jq：够用就行，不引入外部依赖。

支持：
    .                      整个结果
    .answer                取字段
    .data.chats[].title    数组展开后取字段（多个结果逐行输出）
    .data.chats[0]         下标
    .items[].name | .[0]   管道
    .field //              （不支持算术/条件，只做取值）

输出规则和 jq 接近：字符串直接打印，对象/数组打印 JSON（多个结果逐行）。
"""

from __future__ import annotations

import json
import re
from typing import Any

_TOKEN = re.compile(r"\.([A-Za-z_][\w-]*)|\[\s*(\d*)\s*\]")


def _walk(value: Any, expr: str) -> list[Any]:
    """把一段表达式作用到 value 上，返回结果列表（[] 会展开成多个）。"""
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
    """返回适合直接 print 的文本。"""
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
