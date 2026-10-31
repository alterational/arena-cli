"""配置与 profile 解析。

优先级（高 → 低）：命令行 flag > 环境变量 > 配置文件 > 内置默认。
配置文件：`~/.arena-cli/config.json`（或 --config 指定）。
profile：一个 profile = 一套登录会话（state.json + 浏览器用户目录），可以在多账号之间切换。

    ~/.arena-cli/
      config.json              # 全局设置
      state.json  prof/        # profile "default"
      profiles/<name>/state.json,prof/   # 其他 profile
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULTS: dict[str, Any] = {
    "base_url": "https://arena.ai",
    "agent_path": "/agent",
    "timeout": 900,          # 等待 Agent 跑完的上限（秒）
    "stable": 2.5,           # 文本稳定多久算完成
    "profile": "default",
    "format": "text",        # text | json
    "color": True,
    "max_chars": 0,          # 输出长回答时截断（0 = 不截断）
    "selectors": {},         # 透传给引擎的选择器覆盖
    "proxy": "",             # 所有请求走这个代理（http/https/socks5，带 user:pass 也支持）
}

# 可以 arena config set 的键（防手滑写错键名）
SETTABLE = {"base_url", "agent_path", "timeout", "stable", "profile", "format",
            "color", "max_chars", "selectors", "proxy"}


LEGACY_HOME = Path.home() / ".arena-agent"
MIGRATION_MARKER = ".migrated-from-arena-agent"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _merge_tree(src: Path, dst: Path) -> None:
    """把 src 里的东西并进 dst：缺的搬过去，同名文件留更新的那个。"""
    for item in list(src.iterdir()):
        target = dst / item.name
        try:
            if item.is_dir() and not item.is_symlink():
                target.mkdir(parents=True, exist_ok=True)
                _merge_tree(item, target)
            elif target.exists() and _mtime(item) <= _mtime(target):
                continue                      # 目标更新，保留目标
            else:
                target.unlink(missing_ok=True)  # 旧文件不如新文件，覆盖
                item.replace(target)
        except OSError:
            continue                          # 单个文件搬不动就算了（权限/占用）


def _prune_empty(root: Path) -> None:
    for child in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if child.is_dir():
            try:
                child.rmdir()
            except OSError:
                pass
    try:
        root.rmdir()
    except OSError:
        pass


def _relocate_legacy_home(home: Path) -> None:
    """把 2.3.x 之前的 `~/.arena-agent` 并入 `~/.arena-cli`，只做一次。

    两种常见情况都要照顾：
      * 新目录还不存在 → 整个目录改名（最快，登录态和配置原样带走）
      * 新目录已经被建出来了（先跑过其他命令）→ 逐项合并，缺的文件搬过去，
        同名文件留 mtime 更新的那个，所以不会把已有登录态盖掉
    搬完在 home 里写个标记，之后不再重扫 chromium 的大目录。
    """
    if not LEGACY_HOME.is_dir() or (home / MIGRATION_MARKER).exists():
        return
    try:
        if not home.exists():
            LEGACY_HOME.rename(home)
        else:
            _merge_tree(LEGACY_HOME, home)
            _prune_empty(LEGACY_HOME)
    except OSError:
        pass
    try:
        home.mkdir(parents=True, exist_ok=True)
        (home / MIGRATION_MARKER).write_text(
            f'{{"from": "{LEGACY_HOME}", "at": "{datetime.now().isoformat(timespec="seconds")}"}}\n',
            encoding="utf-8")
    except OSError:
        pass


def arena_home() -> Path:
    """数据目录：`~/.arena-cli`（`ARENA_HOME` 可覆盖）。

    2.3.x 之前叫 `~/.arena-agent`：第一次运行会自动迁移（改名或逐项合并），
    登录态、配置、浏览器 profile 都不丢。显式给了 `ARENA_HOME` 就不迁移。
    """
    env = os.environ.get("ARENA_HOME")
    if env:
        return Path(env).expanduser()
    home = Path.home() / ".arena-cli"
    _relocate_legacy_home(home)
    return home


def config_path(explicit: Optional[str] = None) -> Path:
    return Path(explicit).expanduser() if explicit else arena_home() / "config.json"


def read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {"_invalid": True}


def write_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass
class Paths:
    home: Path
    config_file: Path
    profile: str
    state_file: Path
    profile_dir: Path

    @property
    def profiles_dir(self) -> Path:
        return self.home / "profiles"


def profile_paths(name: str, home: Optional[Path] = None) -> tuple[Path, Path]:
    """profile -> (state_file, profile_dir)。default 沿用旧路径，保证向后兼容。"""
    home = home or arena_home()
    if name in ("", "default"):
        return home / "state.json", home / "prof"
    base = home / "profiles" / name
    return base / "state.json", base / "prof"


@dataclass
class Context:
    """命令运行时需要的一切：配置、路径、UI。"""

    cfg: dict[str, Any]
    paths: Paths
    file_cfg: dict[str, Any] = field(default_factory=dict)
    engine_cfg: dict[str, Any] = field(default_factory=dict)
    ui: Any = None
    args: Any = None

    # 便捷访问
    def get(self, key: str, default: Any = None) -> Any:
        return self.cfg.get(key, default)

    # ---- 输出信封：{ok, identity, data, meta}（对齐 lark-cli） ----------------
    def emit(self, data: dict[str, Any], human=None, dry_run: bool = False,
             meta: Optional[dict[str, Any]] = None) -> int:
        jq_expr = getattr(self.args, "jq", None) if self.args is not None else None
        if self.ui.json_mode or jq_expr:
            from . import __version__
            from . import jq as jq_mod

            env: dict[str, Any] = {"ok": True, "identity": self.paths.profile}
            if dry_run:
                env["dry_run"] = True
            env["data"] = data
            env["meta"] = {"version": __version__, **(meta or {})}
            if jq_expr:
                out = jq_mod.render(jq_expr, env)
                if out:
                    print(out)
            else:
                import json as _json
                print(_json.dumps(env, ensure_ascii=False, indent=2))
        elif human is not None:
            human()
        return 0

    def dry_run(self) -> bool:
        return bool(getattr(self.args, "dry_run", False)) if self.args is not None else False


def _env(name: str, default: Any = None) -> Any:
    v = os.environ.get(name)
    return default if v in (None, "") else v


def build_context(args: Any, ui: Any) -> Context:
    """把 flag / env / 文件 / 默认值合成一个 Context。"""
    home = arena_home()
    cfg_file = config_path(getattr(args, "config", None))
    file_cfg = read_config(cfg_file)

    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in file_cfg.items() if not k.startswith("_")})

    # 环境变量
    if _env("ARENA_BASE_URL"):
        cfg["base_url"] = _env("ARENA_BASE_URL")
    if _env("ARENA_PROFILE"):
        cfg["profile"] = _env("ARENA_PROFILE")
    if _env("NO_COLOR"):
        cfg["color"] = False

    # 代理：flag > 配置文件 > ARENA_PROXY > 系统代理环境变量
    if not cfg.get("proxy"):
        cfg["proxy"] = (_env("ARENA_PROXY") or _env("HTTPS_PROXY") or _env("https_proxy")
                        or _env("ALL_PROXY") or _env("all_proxy") or "")

    # 命令行 flag
    for flag, key in (("base_url", "base_url"), ("timeout", "timeout"), ("stable", "stable"),
                      ("profile", "profile"), ("max_chars", "max_chars"), ("proxy", "proxy")):
        value = getattr(args, flag, None)
        if value is not None:
            cfg[key] = value
    if getattr(args, "format", None):
        cfg["format"] = args.format
    if getattr(args, "no_color", False):
        cfg["color"] = False

    profile = str(cfg.get("profile") or "default")
    state_file, profile_dir = profile_paths(profile, home)
    if getattr(args, "state_file", None):
        state_file = Path(args.state_file).expanduser()
    if getattr(args, "profile_dir", None):
        profile_dir = Path(args.profile_dir).expanduser()

    paths = Paths(home=home, config_file=cfg_file, profile=profile,
                  state_file=state_file, profile_dir=profile_dir)

    # 引擎配置：默认值 <- 文件里的 selectors/超时 <- 最终 base_url
    from . import engine as engine_mod

    overrides: dict[str, Any] = {
        "base_url": cfg["base_url"],
        "proxy": cfg.get("proxy") or "",
        "wait_timeout": cfg["timeout"],
        "stable_seconds": cfg["stable"],
        "agent_path": cfg["agent_path"],
    }
    selectors = cfg.get("selectors") or {}
    for key in ("editor_selectors", "send_selectors", "assistant_selectors", "user_selectors",
                "turn_selectors", "streaming_selectors", "answer_stop_markers",
                "auth_cookie_patterns", "anonymous_markers", "logged_in_selectors"):
        if key in selectors:
            overrides[key] = selectors[key]
    engine_cfg = engine_mod.load_config(str(cfg_file) if cfg_file.exists() else None, overrides)

    return Context(cfg=cfg, paths=paths, file_cfg=file_cfg, engine_cfg=engine_cfg,
                   ui=ui, args=args)
