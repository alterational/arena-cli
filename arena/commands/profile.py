"""arena profile list|show|create|use|delete —— 多账号/多套登录态切换。"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..config import arena_home, profile_paths, read_config, write_config


def _profiles() -> list[str]:
    home = arena_home()
    names = {"default"}
    pdir = home / "profiles"
    if pdir.is_dir():
        names |= {p.name for p in pdir.iterdir() if p.is_dir()}
    return sorted(names)


def _status(state_file: Path) -> str:
    if not state_file.exists():
        return "空（未导入 cookies）"
    try:
        from ..engine import parse_cookie_file, summarize_cookies
        cookies, _ = parse_cookie_file(state_file)
        health = summarize_cookies(cookies, {})
        if health["has_auth_cookie"]:
            return f"{health['count']} 条，含 {health['auth_cookie_names'][0]}"
        return f"{health['count']} 条 cookies（无登录凭证）"
    except Exception:  # noqa: BLE001
        return "无法读取"


def run_list(ctx, args) -> int:
    home = arena_home()
    rows = []
    for name in _profiles():
        state_file, _ = profile_paths(name, home)
        rows.append([name, "*" if name == ctx.paths.profile else "", _status(state_file),
                     str(state_file)])
    data = {"active": ctx.paths.profile,
            "profiles": [{"name": r[0], "active": r[1] == "*", "session": r[2],
                          "state_file": r[3]} for r in rows]}

    def human() -> None:
        ctx.ui.table(["profile", "", "会话", "state_file"], rows, title="profiles")
        ctx.ui.info("arena profile use <name> 切换；arena profile create <name> 新建")

    return ctx.emit(data, human=human)


def run_show(ctx, args) -> int:
    data = {"profile": ctx.paths.profile, "state_file": str(ctx.paths.state_file),
            "profile_dir": str(ctx.paths.profile_dir),
            "session": _status(ctx.paths.state_file)}
    return ctx.emit(data, human=lambda: ctx.ui.kv(list(data.items())))


def run_create(ctx, args) -> int:
    name = args.name
    if name == "default":
        ctx.ui.fail("BAD_INPUT", "default 已存在", "换一个名字")
        return 1
    state_file, profile_dir = profile_paths(name)
    if state_file.exists() or profile_dir.exists():
        ctx.ui.fail("BAD_INPUT", f"profile「{name}」已存在", "arena profile list")
        return 1
    profile_dir.mkdir(parents=True, exist_ok=True)
    if args.from_cookies:                      # 直接从 cookies 文件起步
        from ..engine import SkillError, cmd_import
        try:
            res = cmd_import(Path(args.from_cookies).expanduser(), state_file, ctx.engine_cfg)
        except SkillError as exc:
            from ..session import handle
            shutil.rmtree(profile_dir, ignore_errors=True)
            return handle(ctx, exc)
        ctx.ui.ok(f"profile「{name}」已创建，并导入 {res['count']} 条 cookies")
    else:
        ctx.ui.ok(f"profile「{name}」已创建（{profile_dir}）")
        ctx.ui.info(f'下一步：arena profile use {name} && arena cookies import <cookies 文件>')
    return 0


def run_use(ctx, args) -> int:
    name = args.name
    if name not in _profiles():
        ctx.ui.fail("NOT_FOUND", f"没有 profile「{name}」", "arena profile list / arena profile create " + name)
        return 1
    cfg_file = ctx.paths.config_file
    data = read_config(cfg_file)
    data.pop("_invalid", None)
    data["profile"] = name
    write_config(cfg_file, data)
    state_file, _ = profile_paths(name)
    ctx.ui.ok(f"已切换到 profile「{name}」（写入 {cfg_file}）")
    ctx.ui.info(f"会话：{_status(state_file)} → {state_file}")
    return 0


def run_delete(ctx, args) -> int:
    name = args.name
    if name in ("default",):
        ctx.ui.fail("BAD_INPUT", "不能删除 default profile", "可以直接 arena cookies show 查看它")
        return 1
    state_file, profile_dir = profile_paths(name)
    if not state_file.exists() and not profile_dir.exists():
        ctx.ui.fail("NOT_FOUND", f"没有 profile「{name}」", "arena profile list")
        return 1
    root = ctx.paths.home / "profiles" / name
    if ctx.dry_run():
        return ctx.emit({"action": "delete", "profile": name, "path": str(root)},
                        dry_run=True,
                        human=lambda: ctx.ui.info(f"[dry-run] 会删除 {root}（含会话与浏览器数据）"))
    if not getattr(args, "yes", False):
        ctx.ui.warn(f"会删除 {root}（含会话与浏览器数据）。确认请加 --yes")
        return 1
    shutil.rmtree(root, ignore_errors=True)      # 整个 profile 目录，别留空壳
    shutil.rmtree(profile_dir, ignore_errors=True)
    state_file.unlink(missing_ok=True)
    if ctx.get("profile") == name:
        cfg_file = ctx.paths.config_file
        data = read_config(cfg_file)
        data["profile"] = "default"
        write_config(cfg_file, data)
        ctx.ui.info("已把默认 profile 切回 default")
    return ctx.emit({"profile": name, "deleted": True, "path": str(root)},
                    human=lambda: ctx.ui.ok(f"已删除 profile「{name}」"))
