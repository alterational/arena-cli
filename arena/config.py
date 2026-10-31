"""配置与 profile 解析。

优先级（高 → 低）：命令行 flag > 环境变量 > 配置文件 > 内置默认。
配置文件：`~/.arena-agent/config.json`（或 --config 指定）。
profile：一个 profile = 一套登录会话（state.json + 浏览器用户目录），可以在多账号之间切换。

    ~/.arena-agent/
      config.json              # 全局设置
      state.json  prof/        # profile "default"
      profiles/<name>/state.json,prof/   # 其他 profile
"""

from __future__ import annotations

import json
import os
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


def arena_home() -> Path:
    return Path(os.environ.get("ARENA_HOME") or (Path.home() / ".arena-agent")).expanduser()


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
