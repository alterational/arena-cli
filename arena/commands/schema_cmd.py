"""arena schema — command descriptions for agent introspection."""

from __future__ import annotations

from .. import spec
from ..errors import ArenaError


def _expand_path(path: list[str]) -> list[str]:
    """`schema workspace.download` -> ["workspace", "download"] (dotted notation is also accepted)."""
    out: list[str] = []
    for raw in path:
        out.extend(part for part in str(raw).split(".") if part)
    return out


def run(ctx, args) -> int:
    # Deferred import to avoid a circular dependency with cli
    from ..cli import RISK, build_parser

    parser = build_parser()
    path = _expand_path(list(getattr(args, "path", []) or []))

    if not path:                                  # No path given: list all commands
        commands = []
        for name in spec.available_commands(parser):
            target, _ = spec.find(parser, [name])
            children = spec.available_commands(target) if target else []
            commands.append({"command": name, "risk": RISK.get(name, "read"),
                             "subcommands": children})
        data = {"commands": commands, "usage": "arena schema <command> [subcommand]"}
        def human() -> None:
            ctx.ui.table(["command", "risk", "subcommands"],
                         [[c["command"], c["risk"],
                           ", ".join(c["subcommands"]) or "-"] for c in commands],
                         title="arena schema")
            ctx.ui.info("To see details of a command: arena schema ask / arena schema cookies import")
        return ctx.emit(data, human=human)

    target, walked = spec.find(parser, path)
    if not walked:
        raise ArenaError("validation", "unknown_command", f"Unknown command: {' '.join(path)}",
                         "Available commands: " + ", ".join(spec.available_commands(parser)), exit_code=USAGE_ERR)
    if len(walked) < len(path):                  # The first segments are valid; the last one does not exist
        subs = spec.available_commands(target) if target is not None else []
        raise ArenaError(
            "validation", "unknown_command",
            f"Unknown subcommand: {' '.join(path[len(walked):])} (under {walked[-1]})",
            "Available subcommands: " + (", ".join(subs) or "(none)"), exit_code=USAGE_ERR)
    info = spec.describe(parser, path, RISK)

    def human() -> None:
        ctx.ui.kv([
            ("command", info["command"]),
            ("risk", info["risk"] + (" (requires --yes)" if info["risk"] == "high-risk-write" else "")),
            ("summary", info["summary"]),
        ])
        params = info["params"]
        if params:
            ctx.ui.table(["parameter", "required", "default", "description"],
                         [[p.get("flag") or p.get("arg"),
                           "yes" if p.get("required") else "",
                           str(p.get("default", "")), p.get("help", "")[:46]] for p in params],
                         title="\nParameters")
            ctx.ui.info("Global options (--json / --jq / --dry-run / --yes / --profile / --timeout …) apply to all commands")
        if info.get("subcommands"):
            ctx.ui.table(["subcommand"], [[s] for s in info["subcommands"]], title="\nSubcommands")

    return ctx.emit(info, human=human)


USAGE_ERR = 64
