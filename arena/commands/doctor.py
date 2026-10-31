"""arena doctor / arena version —— 环境体检与版本信息。"""

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
        return False, "未安装", "pip install playwright && python3 -m playwright install chromium"
    except Exception as exc:  # noqa: BLE001
        return False, f"异常：{exc}", "重新安装 playwright"


def _check_chromium() -> tuple[bool, str, str]:
    cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or (Path.home() / ".cache" / "ms-playwright"))
    if not cache.exists():
        return False, f"没找到 {cache}", "python3 -m playwright install chromium"
    hits = [p.name for p in cache.iterdir() if p.name.startswith("chromium")]
    if not hits:
        return False, "浏览器目录为空", "python3 -m playwright install chromium"
    return True, ", ".join(hits[:2]), ""


def _check_network(base_url: str, proxy: str = "") -> tuple[bool, str, str]:
    try:
        req = urllib.request.Request(base_url, method="HEAD",
                                     headers={"user-agent": "arena-cli/doctor"})
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
            resp_ctx = opener.open(req, timeout=8)
            note = "（经代理）"
        else:
            resp_ctx = urllib.request.urlopen(req, timeout=6)
            note = ""
        with resp_ctx as resp:
            return True, f"HTTP {resp.status}{note}", ""
    except urllib.error.HTTPError as exc:
        return True, f"HTTP {exc.code}（能连通）", ""
    except Exception as exc:  # noqa: BLE001
        return False, f"连不上：{str(exc)[:60]}", "检查网络/代理；公司网可能拦 HEAD 请求"


def _check_session(ctx) -> tuple[bool, str, str]:
    path = ctx.paths.state_file
    if not path.exists():
        return False, f"没有会话（{path}）", "arena cookies import <cookies 文件> 或 arena login"
    try:
        cookies, meta = parse_cookie_file(path)
    except Exception as exc:  # noqa: BLE001
        return False, f"读不出：{exc}", "重新导入 cookies"
    health = summarize_cookies(cookies, ctx.engine_cfg)
    if health["has_auth_cookie"]:
        return True, f"{health['count']} 条，含 {'/'.join(health['auth_cookie_names'][:2])}", ""
    return False, f"{health['count']} 条，但没有登录凭证", "重新导出 cookies（要含 arena-auth-*）"


def _check_config(ctx) -> tuple[bool, str, str]:
    if not ctx.paths.config_file.exists():
        return True, "未创建（用默认值）", ""
    if ctx.file_cfg.get("_invalid"):
        return False, "JSON 解析失败", f"修好或删掉 {ctx.paths.config_file}"
    return True, str(ctx.paths.config_file), ""


def _check_skill(ctx) -> tuple[bool, str, str]:
    target = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")) / "skills" / "arena-agent"
    if target.exists():
        return True, f"已装在 {target}", ""
    return False, "未安装为 Hermes 技能", "./install.sh"


def run_doctor(ctx, args) -> int:
    rows = [
        ("python", True, platform.python_version(), ""),
        ("playwright", *_check_playwright()),
        ("chromium", *_check_chromium()),
        ("network", *_check_network(str(ctx.get("base_url")), str(ctx.get("proxy") or ""))),
        ("config", *_check_config(ctx)),
        ("session", *_check_session(ctx)),
        ("profile", True, ctx.paths.profile, ""),
        ("proxy", True, ctx.get("proxy") or "（未配置，直连）", ""),
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
        "hint": ("环境看起来没问题" if healthy else
                 f"有 {len(problems)} 项需要处理，按 problems[].fix 修（多为依赖/浏览器未装）"),
    }

    def human() -> None:
        ctx.ui.table(["检查项", "结果", "说明"],
                     [[c["name"], ctx.ui.paint("✓", "green") if c["ok"] else ctx.ui.paint("✗", "red"),
                       c["detail"]] for c in checks],
                     title=f"arena doctor（v{__version__}）")
        if problems:
            print()
            for p in problems:
                if p["fix"]:
                    ctx.ui.warn(f"{p['check']}：{p['fix']}")
        else:
            ctx.ui.ok("环境看起来没问题")

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
        "playwright": pw or "未安装",
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
