"""arena schema —— 给 agent 自省的命令说明。"""

from __future__ import annotations

from .. import spec
from ..errors import ArenaError


def _expand_path(path: list[str]) -> list[str]:
    """`schema workspace.download` -> ["workspace", "download"]（点分写法也认）。"""
    out: list[str] = []
    for raw in path:
        out.extend(part for part in str(raw).split(".") if part)
    return out


def run(ctx, args) -> int:
    # 延迟导入，避免和 cli 循环依赖
    from ..cli import RISK, build_parser

    parser = build_parser()
    path = _expand_path(list(getattr(args, "path", []) or []))

    if not path:                                  # 没给路径：列全部命令
        commands = []
        for name in spec.available_commands(parser):
            target, _ = spec.find(parser, [name])
            children = spec.available_commands(target) if target else []
            commands.append({"command": name, "risk": RISK.get(name, "read"),
                             "subcommands": children})
        data = {"commands": commands, "usage": "arena schema <命令> [子命令]"}
        def human() -> None:
            ctx.ui.table(["命令", "风险", "子命令"],
                         [[c["command"], c["risk"],
                           ", ".join(c["subcommands"]) or "-"] for c in commands],
                         title="arena schema")
            ctx.ui.info("细看某个命令：arena schema ask / arena schema cookies import")
        return ctx.emit(data, human=human)

    target, walked = spec.find(parser, path)
    if not walked:
        raise ArenaError("validation", "unknown_command", f"未知命令：{' '.join(path)}",
                         "可用命令：" + ", ".join(spec.available_commands(parser)), exit_code=USAGE_ERR)
    if len(walked) < len(path):                  # 前几段对、后面那段不存在
        subs = spec.available_commands(target) if target is not None else []
        raise ArenaError(
            "validation", "unknown_command",
            f"未知子命令：{' '.join(path[len(walked):])}（在 {walked[-1]} 下）",
            "可用子命令：" + (", ".join(subs) or "（无）"), exit_code=USAGE_ERR)
    info = spec.describe(parser, path, RISK)

    def human() -> None:
        ctx.ui.kv([
            ("命令", info["command"]),
            ("风险", info["risk"] + ("（需要 --yes）" if info["risk"] == "high-risk-write" else "")),
            ("说明", info["summary"]),
        ])
        params = info["params"]
        if params:
            ctx.ui.table(["参数", "必填", "默认", "说明"],
                         [[p.get("flag") or p.get("arg"),
                           "是" if p.get("required") else "",
                           str(p.get("default", "")), p.get("help", "")[:46]] for p in params],
                         title="\n参数")
            ctx.ui.info("全局参数（--json / --jq / --dry-run / --yes / --profile / --timeout …）对所有命令生效")
        if info.get("subcommands"):
            ctx.ui.table(["子命令"], [[s] for s in info["subcommands"]], title="\n子命令")

    return ctx.emit(info, human=human)


USAGE_ERR = 64
