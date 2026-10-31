"""arena config list|get|set|unset|path —— 读写 ~/.arena-cli/config.json。"""

from __future__ import annotations

import json

from ..config import SETTABLE, read_config, write_config


def _coerce(raw: str):
    """命令行给的都是字符串，按 JSON 规则还原类型（123 / true / ["a"] / 文本）。"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def run_list(ctx, args) -> int:
    data = {"config_file": str(ctx.paths.config_file),
            "values": {k: ctx.get(k) for k in sorted(SETTABLE)},
            "effective": {"profile": ctx.paths.profile,
                          "state_file": str(ctx.paths.state_file),
                          "profile_dir": str(ctx.paths.profile_dir)}}

    def human() -> None:
        ctx.ui.kv([(k, v) for k, v in data["values"].items()],
                  title=f"配置（{ctx.paths.config_file}）")
        ctx.ui.kv(list(data["effective"].items()), title="\n当前生效")

    return ctx.emit(data, human=human)


def run_get(ctx, args) -> int:
    key = args.key
    if key not in SETTABLE:
        ctx.ui.fail("BAD_INPUT", f"未知配置项：{key}", "可用项：" + ", ".join(sorted(SETTABLE)))
        return 1
    value = ctx.get(key)
    return ctx.emit({key: value}, human=lambda: print(
        json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value))


def run_set(ctx, args) -> int:
    key = args.key
    if key not in SETTABLE:
        ctx.ui.fail("BAD_INPUT", f"未知配置项：{key}", "可用项：" + ", ".join(sorted(SETTABLE)))
        return 1
    if ctx.dry_run():
        return ctx.emit({"key": key, "value": _coerce(args.value),
                         "config_file": str(ctx.paths.config_file)}, dry_run=True,
                        human=lambda: ctx.ui.info(
                            f"[dry-run] 会把 {key} 设为 {args.value} → {ctx.paths.config_file}"))

    data = read_config(ctx.paths.config_file)
    data.pop("_invalid", None)
    value = _coerce(args.value)
    if key == "selectors" and not isinstance(value, dict):
        ctx.ui.fail("BAD_INPUT", "selectors 必须是 JSON 对象", '例：arena config set selectors \'{"editor_selectors":["..."]}\'')
        return 1
    data[key] = value
    write_config(ctx.paths.config_file, data)
    return ctx.emit({"key": key, "value": value, "config_file": str(ctx.paths.config_file)},
                    human=lambda: ctx.ui.ok(
                        f"{key} = {json.dumps(value, ensure_ascii=False)} → {ctx.paths.config_file}"))


def run_unset(ctx, args) -> int:
    data = read_config(ctx.paths.config_file)
    if args.key not in data:
        ctx.ui.warn(f"{args.key} 未设置，无需删除")
        return 0
    data.pop(args.key, None)
    write_config(ctx.paths.config_file, data)
    ctx.ui.ok(f"已删除 {args.key}（回到默认值）")
    return 0


def run_path(ctx, args) -> int:
    print(ctx.paths.config_file)
    return 0
