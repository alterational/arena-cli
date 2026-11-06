"""Configuration and profile resolution.

Precedence (high → low): command-line flag > environment variable > configuration file > built-in default.
Configuration file: `~/.arena-cli/config.json` (or specified with --config).
Profile: one profile = one login session (state.json + browser user-data directory); you can switch between accounts.

    ~/.arena-cli/
      config.json              # Global settings
      state.json  prof/        # profile "default"
      profiles/<name>/state.json,prof/   # Other profiles
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
    "timeout": 900,          # Upper limit (seconds) for waiting for the Agent to finish
    "stable": 2.5,           # How long text must stay unchanged to count as complete
    "profile": "default",
    "format": "text",        # text | json
    "color": True,
    "max_chars": 0,          # Truncate long answers when printing (0 = no truncation)
    "selectors": {},         # Selector overrides passed through to the engine
    "proxy": "",             # Route all requests through this proxy
    "block_analytics_scripts": True,
    "reduce_visual_effects": True,
}

# Keys that can be set with arena config set (prevents typos in key names)
SETTABLE = {"base_url", "agent_path", "timeout", "stable", "profile", "format",
            "color", "max_chars", "selectors", "proxy", "block_analytics_scripts", "reduce_visual_effects"}


LEGACY_HOME = Path.home() / ".arena-agent"
MIGRATION_MARKER = ".migrated-from-arena-agent"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _merge_tree(src: Path, dst: Path) -> None:
    """Merge the contents of src into dst: move missing items over; for same-named files, keep the newer one."""
    for item in list(src.iterdir()):
        target = dst / item.name
        try:
            if item.is_dir() and not item.is_symlink():
                target.mkdir(parents=True, exist_ok=True)
                _merge_tree(item, target)
            elif target.exists() and _mtime(item) <= _mtime(target):
                continue                      # Target is newer; keep the target
            else:
                target.unlink(missing_ok=True)  # The old file is older than the new one; overwrite
                item.replace(target)
        except OSError:
            continue                          # Skip any single file that cannot be moved (permissions / in use)


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
    """Merge the pre-2.3.x `~/.arena-agent` into `~/.arena-cli`, only once.

    Two common cases are handled:
      * The new directory does not exist yet → rename the whole directory (fastest; login session and config carry over unchanged)
      * The new directory has already been created (another command ran first) → merge item by item: missing files are moved over,
        and for same-named files the one with the newer mtime is kept, so an existing login session is never overwritten
    After migration, a marker is written in home so the large chromium directories are not rescanned later.
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
    """Data directory: `~/.arena-cli` (can be overridden with `ARENA_HOME`).

    Before 2.3.x it was called `~/.arena-agent`: the first run migrates it automatically (rename or item-by-item merge),
    so login sessions, configuration, and browser profiles are all preserved. Explicitly setting `ARENA_HOME` disables migration.
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
    from .storage import write_private_text
    write_private_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


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
    """profile -> (state_file, profile_dir). "default" keeps the legacy paths for backward compatibility."""
    home = home or arena_home()
    if name in ("", "default"):
        return home / "state.json", home / "prof"
    from .errors import ArenaError
    if name in (".", "..") or any(c in name for c in ("/", "\\", "\0")):
        raise ArenaError("validation", "invalid_argument", "Invalid profile name")
    root = home / "profiles"
    base = root / name
    if root.is_symlink() or base.is_symlink() or base.resolve().parent != root.resolve():
        raise ArenaError("validation", "invalid_argument", "Profile path must stay inside the profiles directory")
    return base / "state.json", base / "prof"


@dataclass
class Context:
    """Everything a command needs at runtime: configuration, paths, and UI."""

    cfg: dict[str, Any]
    paths: Paths
    file_cfg: dict[str, Any] = field(default_factory=dict)
    engine_cfg: dict[str, Any] = field(default_factory=dict)
    ui: Any = None
    args: Any = None

    # Convenient access
    def get(self, key: str, default: Any = None) -> Any:
        return self.cfg.get(key, default)

    # ---- Output envelope: {ok, identity, data, meta} (aligned with lark-cli) ----------------
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
    """Combine flags / environment / file / defaults into a single Context."""
    home = arena_home()
    cfg_file = config_path(getattr(args, "config", None))
    file_cfg = read_config(cfg_file)
    if file_cfg.get("_invalid"):
        from .errors import ArenaError
        raise ArenaError("config", "invalid_config", f"Invalid JSON in {cfg_file}", "Fix the configuration file and try again")

    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in file_cfg.items() if not k.startswith("_")})

    # Environment variables
    if _env("ARENA_BASE_URL"):
        cfg["base_url"] = _env("ARENA_BASE_URL")
    if _env("ARENA_PROFILE"):
        cfg["profile"] = _env("ARENA_PROFILE")
    if _env("NO_COLOR"):
        cfg["color"] = False

    # Proxy: flag > configuration file > ARENA_PROXY > system proxy environment variables
    if not cfg.get("proxy"):
        cfg["proxy"] = (_env("ARENA_PROXY") or _env("HTTPS_PROXY") or _env("https_proxy")
                        or _env("ALL_PROXY") or _env("all_proxy") or "")

    # Command-line flags
    for flag, key in (("base_url", "base_url"), ("timeout", "timeout"), ("stable", "stable"),
                      ("profile", "profile"), ("max_chars", "max_chars"), ("proxy", "proxy")):
        value = getattr(args, flag, None)
        if value is not None:
            cfg[key] = value
    if getattr(args, "format", None):
        cfg["format"] = args.format
    if getattr(args, "no_color", False):
        cfg["color"] = False

    from .url_safety import validate_base_url
    try:
        validate_base_url(cfg["base_url"])
    except (ValueError, TypeError):
        from .errors import ArenaError
        raise ArenaError("validation", "invalid_argument", "Invalid base URL", "Use an HTTP(S) site origin without credentials, path, query, or fragment") from None
    profile = str(cfg.get("profile") or "default")
    state_file, profile_dir = profile_paths(profile, home)
    if getattr(args, "state_file", None):
        state_file = Path(args.state_file).expanduser()
    if getattr(args, "profile_dir", None):
        profile_dir = Path(args.profile_dir).expanduser()

    paths = Paths(home=home, config_file=cfg_file, profile=profile,
                  state_file=state_file, profile_dir=profile_dir)

    # Engine configuration: defaults <- selectors/timeouts in the file <- final base_url
    from . import engine as engine_mod

    overrides: dict[str, Any] = {
        "base_url": cfg["base_url"],
        "proxy": cfg.get("proxy") or "",
        "wait_timeout": cfg["timeout"],
        "stable_seconds": cfg["stable"],
        "agent_path": cfg["agent_path"],
        "block_analytics_scripts": cfg["block_analytics_scripts"],
        "reduce_visual_effects": cfg["reduce_visual_effects"],
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
