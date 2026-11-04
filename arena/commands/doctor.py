"""arena doctor / arena version — environment check and version information."""

from __future__ import annotations

import json
import os
import platform
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .. import __version__
from ..engine import parse_cookie_file, summarize_cookies


def _check_playwright() -> tuple[bool, str, str]:
    try:
        import playwright  # noqa: F401
        from importlib.metadata import version
        return True, f"playwright {version('playwright')}", ""
    except ImportError:
        return False, "not installed", "pip install playwright && python3 -m playwright install chromium"
    except Exception as exc:  # noqa: BLE001
        return False, f"error: {exc}", "reinstall playwright"


def _check_chromium() -> tuple[bool, str, str]:
    cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or (Path.home() / ".cache" / "ms-playwright"))
    if not cache.exists():
        return False, f"{cache} not found", "python3 -m playwright install chromium"
    hits = [p.name for p in cache.iterdir() if p.name.startswith("chromium")]
    if not hits:
        return False, "browser directory is empty", "python3 -m playwright install chromium"
    return True, ", ".join(hits[:2]), ""


def _check_network(base_url: str, proxy: str = "") -> tuple[bool, str, str]:
    try:
        req = urllib.request.Request(base_url, method="HEAD",
                                     headers={"user-agent": "arena-cli/doctor"})
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
            resp_ctx = opener.open(req, timeout=8)
            note = " (via proxy)"
        else:
            resp_ctx = urllib.request.urlopen(req, timeout=6)
            note = ""
        with resp_ctx as resp:
            return True, f"HTTP {resp.status}{note}", ""
    except urllib.error.HTTPError as exc:
        return True, f"HTTP {exc.code} (reachable)", ""
    except Exception as exc:  # noqa: BLE001
        return False, f"unreachable: {str(exc)[:60]}", "Check the network / proxy; corporate networks may block HEAD requests"


def _check_session(ctx) -> tuple[bool, str, str]:
    path = ctx.paths.state_file
    if not path.exists():
        return False, f"no session ({path})", "arena cookies import <cookies file> or arena login"
    try:
        cookies, meta = parse_cookie_file(path)
    except Exception as exc:  # noqa: BLE001
        return False, f"could not read: {exc}", "re-import the cookies"
    health = summarize_cookies(cookies, ctx.engine_cfg)
    if health["has_auth_cookie"]:
        return True, f"{health['count']} cookies, including {'/'.join(health['auth_cookie_names'][:2])}", ""
    return False, f"{health['count']} cookies, but no login credential", "re-export the cookies (must include arena-auth-*)"


def _check_config(ctx) -> tuple[bool, str, str]:
    if not ctx.paths.config_file.exists():
        return True, "not created (using defaults)", ""
    if ctx.file_cfg.get("_invalid"):
        return False, "JSON parse failed", f"fix or delete {ctx.paths.config_file}"
    return True, str(ctx.paths.config_file), ""


def _check_skill(ctx) -> tuple[bool, str, str]:
    root = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")) / "skills"
    target = root / "arena-cli"
    if target.exists():
        return True, f"installed at {target}", ""
    legacy = root / "arena-agent"                       # Name used before 2.3.x
    if legacy.exists():
        return False, f"only the old skill directory was found: {legacy}", "./install.sh (then delete the old skills/arena-agent)"
    return False, "not installed as a Hermes skill", "./install.sh"


def run_doctor(ctx, args) -> int:
    rows = [
        ("python", True, platform.python_version(), ""),
        ("playwright", *_check_playwright()),
        ("chromium", *_check_chromium()),
        ("network", *_check_network(str(ctx.get("base_url")), str(ctx.get("proxy") or ""))),
        ("config", *_check_config(ctx)),
        ("session", *_check_session(ctx)),
        ("profile", True, ctx.paths.profile, ""),
        ("proxy", True, ctx.get("proxy") or "(not configured; direct connection)", ""),
        ("hermes skill", *_check_skill(ctx)),
    ]
    checks = [{"name": n, "ok": ok, "detail": d, "fix": h} for n, ok, d, h in rows]
    problems = [{"check": c["name"], "fix": c["fix"]} for c in checks if not c["ok"]]
    healthy = not problems

    data = {
        "healthy": healthy,
        "version": __version__,
        "checks": checks,
        "problems": problems,
        "hint": ("Environment looks fine" if healthy else
                 f"{len(problems)} item(s) need attention; fix them per problems[].fix (mostly missing dependencies / browser)"),
    }

    def human() -> None:
        ctx.ui.table(["check", "result", "details"],
                     [[c["name"], ctx.ui.paint("✓", "green") if c["ok"] else ctx.ui.paint("✗", "red"),
                       c["detail"]] for c in checks],
                     title=f"arena doctor (v{__version__})")
        if problems:
            print()
            for p in problems:
                if p["fix"]:
                    ctx.ui.warn(f"{p['check']}: {p['fix']}")
        else:
            ctx.ui.ok("Environment looks fine")

    ctx.emit(data, human=human)
    return 0 if healthy else 1


def run_version(ctx, args) -> int:
    import importlib.metadata as md

    try:
        pw = md.version("playwright")
    except Exception:  # noqa: BLE001
        pw = None

    data = {
        "arena": __version__,
        "python": platform.python_version(),
        "platform": sys.platform,
        "playwright": pw or "not installed",
        "profile": ctx.paths.profile,
        "state_file": str(ctx.paths.state_file),
        "base_url": ctx.get("base_url"),
    }

    def human() -> None:
        print(f"arena {data['arena']}")
        print(f"  python     {data['python']} ({data['platform']})")
        print(f"  playwright {data['playwright']}")
        print(f"  profile    {data['profile']}  →  {data['state_file']}")
        print(f"  target     {data['base_url']}")

    return ctx.emit(data, human=human)
