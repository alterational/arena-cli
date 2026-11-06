"""arena profile list|show|create|use|delete — switch between multiple accounts / login sessions."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..config import arena_home, profile_paths, read_config, write_config


def _profiles() -> list[str]:
    home = arena_home()
    names = {"default"}
    pdir = home / "profiles"
    if pdir.is_dir():
        names |= {p.name for p in pdir.iterdir() if p.is_dir() and not p.is_symlink()}
    return sorted(names)


def _status(state_file: Path, cfg: dict | None = None) -> str:
    if not state_file.exists():
        return "empty (no cookies imported)"
    try:
        from ..engine import DEFAULT_CONFIG, parse_cookie_file, summarize_cookies
        cookies, _ = parse_cookie_file(state_file)
        health = summarize_cookies(cookies, cfg if cfg is not None else DEFAULT_CONFIG)
        if health["has_auth_cookie"]:
            return f"{health['count']} cookies, including {health['auth_cookie_names'][0]}"
        return f"{health['count']} cookies (no login credential)"
    except Exception:  # noqa: BLE001
        return "unreadable"


def run_list(ctx, args) -> int:
    home = arena_home()
    rows = []
    for name in _profiles():
        state_file, _ = profile_paths(name, home)
        rows.append([name, "*" if name == ctx.paths.profile else "", _status(state_file, ctx.engine_cfg),
                     str(state_file)])
    data = {"active": ctx.paths.profile,
            "profiles": [{"name": r[0], "active": r[1] == "*", "session": r[2],
                          "state_file": r[3]} for r in rows]}

    def human() -> None:
        ctx.ui.table(["profile", "", "session", "state_file"], rows, title="profiles")
        ctx.ui.info("arena profile use <name> to switch; arena profile create <name> to create a new one")

    return ctx.emit(data, human=human)


def run_show(ctx, args) -> int:
    data = {"profile": ctx.paths.profile, "state_file": str(ctx.paths.state_file),
            "profile_dir": str(ctx.paths.profile_dir),
            "session": _status(ctx.paths.state_file, ctx.engine_cfg)}
    return ctx.emit(data, human=lambda: ctx.ui.kv(list(data.items())))


def run_create(ctx, args) -> int:
    name = args.name
    if name == "default":
        ctx.ui.fail("BAD_INPUT", "default already exists", "Choose a different name")
        return 1
    state_file, profile_dir = profile_paths(name)
    if state_file.exists() or profile_dir.exists():
        ctx.ui.fail("BAD_INPUT", f"profile \"{name}\" already exists", "arena profile list")
        return 1
    profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.from_cookies:                      # Start directly from a cookies file
        from ..engine import SkillError, cmd_import
        try:
            res = cmd_import(Path(args.from_cookies).expanduser(), state_file, ctx.engine_cfg)
        except SkillError as exc:
            from ..session import handle
            shutil.rmtree(profile_dir, ignore_errors=True)
            return handle(ctx, exc)
        ctx.ui.ok(f"profile \"{name}\" created and {res['count']} cookies imported")
    else:
        ctx.ui.ok(f"profile \"{name}\" created ({profile_dir})")
        ctx.ui.info(f'Next step: arena profile use {name} && arena cookies import <cookies file>')
    return 0


def run_use(ctx, args) -> int:
    name = args.name
    if name not in _profiles():
        ctx.ui.fail("NOT_FOUND", f"No profile \"{name}\"", "arena profile list / arena profile create " + name)
        return 1
    cfg_file = ctx.paths.config_file
    data = read_config(cfg_file)
    data.pop("_invalid", None)
    data["profile"] = name
    write_config(cfg_file, data)
    state_file, _ = profile_paths(name)
    ctx.ui.ok(f"Switched to profile \"{name}\" (written to {cfg_file})")
    ctx.ui.info(f"Session: {_status(state_file, ctx.engine_cfg)} → {state_file}")
    return 0


def run_delete(ctx, args) -> int:
    name = args.name
    if name in ("default",):
        ctx.ui.fail("BAD_INPUT", "Cannot delete the default profile", "You can view it with arena cookies show")
        return 1
    state_file, profile_dir = profile_paths(name)
    if not state_file.exists() and not profile_dir.exists():
        ctx.ui.fail("NOT_FOUND", f"No profile \"{name}\"", "arena profile list")
        return 1
    root = ctx.paths.home / "profiles" / name
    if ctx.dry_run():
        return ctx.emit({"action": "delete", "profile": name, "path": str(root)},
                        dry_run=True,
                        human=lambda: ctx.ui.info(f"[dry-run] would delete {root} (including session and browser data)"))
    if not getattr(args, "yes", False):
        ctx.ui.warn(f"This will delete {root} (including the session and browser data). Add --yes to confirm")
        return 1
    shutil.rmtree(root, ignore_errors=True)      # Remove the entire profile directory; leave no empty shell
    shutil.rmtree(profile_dir, ignore_errors=True)
    state_file.unlink(missing_ok=True)
    if ctx.get("profile") == name:
        cfg_file = ctx.paths.config_file
        data = read_config(cfg_file)
        data["profile"] = "default"
        write_config(cfg_file, data)
        ctx.ui.info("Switched the default profile back to default")
    return ctx.emit({"profile": name, "deleted": True, "path": str(root)},
                    human=lambda: ctx.ui.ok(f"Deleted profile \"{name}\""))
