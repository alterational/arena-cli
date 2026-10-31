"""`arena schema` —— 给 agent 自省用的命令说明（对齐 lark-cli schema 的思路）。

不另外维护一份文档：直接从 argparse 的 parser 反射出参数、类型、默认值、风险等级。
"""

from __future__ import annotations

import argparse
from typing import Any, Optional

TYPE_NAMES = {None: "string", str: "string", int: "int", float: "float", bool: "bool"}

# 这些全局 flag 每条命令都能用，schema 里单独列，不塞进每个命令的参数表
GLOBAL_FLAGS = {
    "-h", "--help", "--json", "--format", "--jq", "-q", "--quiet", "-v", "--verbose",
    "--no-color", "--dry-run", "--yes", "--no-wait", "--profile", "--config",
    "--base-url", "--timeout", "--stable", "--max-chars", "--cookies", "--headed", "--proxy",
    "--state-file", "--profile-dir", "--version",
}


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:  # noqa: SLF001 — argparse 没有公开 API
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return action.choices  # type: ignore[return-value]
    return {}


def available_commands(parser: argparse.ArgumentParser) -> list[str]:
    return sorted(_subparsers(parser))


def find(parser: argparse.ArgumentParser, path: list[str]
         ) -> tuple[Optional[argparse.ArgumentParser], list[str]]:
    """按路径找到子 parser；返回 (parser, 已走通的路径)。"""
    current = parser
    walked: list[str] = []
    for name in path:
        children = _subparsers(current)
        if name not in children:
            break
        current = children[name]
        walked.append(name)
    return current, walked


def _params(parser: argparse.ArgumentParser, risk: str) -> list[dict[str, Any]]:
    params: list[dict[str, Any]] = []
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):  # noqa: SLF001
            continue
        if any(f in GLOBAL_FLAGS for f in action.option_strings):
            continue
        long_flags = [f for f in action.option_strings if f.startswith("--")] or action.option_strings
        if long_flags:
            is_bool = isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction))  # noqa: SLF001
            kind = "bool" if is_bool else TYPE_NAMES.get(action.type, "string")
            flag = ", ".join(long_flags)
            if not is_bool:
                flag += " <" + kind + ">"
            entry: dict[str, Any] = {
                "flag": flag,
                "type": kind,
                "required": bool(action.required),
                "help": (action.help or "").split("（")[0],
            }
            if action.choices:
                entry["choices"] = list(action.choices)
                entry["flag"] = ", ".join(long_flags) + " <" + "|".join(map(str, action.choices)) + ">"
            default = action.default
            if default not in (None, argparse.SUPPRESS, False) and default != "==SUPPRESS==":
                entry["default"] = default
            params.append(entry)
        elif action.dest not in ("help",):
            optional = getattr(action, "nargs", None) in ("?", "*")
            params.append({
                "arg": action.dest,
                "type": "string",
                "required": not optional,
                "help": (action.help or "").split("（")[0],
            })
    return params


def describe(parser: argparse.ArgumentParser, path: list[str], risks: dict
             ) -> dict[str, Any]:
    target, walked = find(parser, path)
    joined = ".".join(walked)
    risk = risks.get(joined) or risks.get(walked[0] if walked else "") or "read"
    children = _subparsers(target) if target else {}
    out: dict[str, Any] = {
        "command": "arena " + " ".join(walked) if walked else "arena",
        "risk": risk,
        "usage": target.format_usage().strip() if target else "arena --help",
        "summary": (target.description or target.format_help().split("\n\n")[0]).strip()
        if target else "—",
        "params": _params(target, risk) if target else [],
    }
    # 把"完整路径"的提示也带上，方便 agent 拼命令
    if children:
        out["subcommands"] = sorted(children)
    return out
