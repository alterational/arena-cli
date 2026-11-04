"""`arena schema` — command descriptions for agent introspection (follows the approach of lark-cli schema).

No separate documentation is maintained: parameters, types, defaults, and risk levels are reflected directly from the argparse parser.
"""

from __future__ import annotations

import argparse
from typing import Any, Optional

TYPE_NAMES = {None: "string", str: "string", int: "int", float: "float", bool: "bool"}

# These global flags work with every command; schema lists them separately instead of repeating them in each command's parameter table
GLOBAL_FLAGS = {
    "-h", "--help", "--json", "--format", "--jq", "-q", "--quiet", "-v", "--verbose",
    "--no-color", "--dry-run", "--yes", "--no-wait", "--profile", "--config",
    "--base-url", "--timeout", "--stable", "--max-chars", "--cookies", "--headed", "--proxy",
    "--state-file", "--profile-dir", "--version",
}


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:  # noqa: SLF001 — argparse has no public API
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return action.choices  # type: ignore[return-value]
    return {}


def available_commands(parser: argparse.ArgumentParser) -> list[str]:
    return sorted(_subparsers(parser))


def find(parser: argparse.ArgumentParser, path: list[str]
         ) -> tuple[Optional[argparse.ArgumentParser], list[str]]:
    """Find the subparser for a path; returns (parser, the path that was successfully walked)."""
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
                "help": (action.help or "").split(" (")[0],
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
                "help": (action.help or "").split(" (")[0],
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
    # Also include the "full path" hint so agents can assemble commands more easily
    if children:
        out["subcommands"] = sorted(children)
    return out
