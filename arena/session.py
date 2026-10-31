"""打开浏览器会话的小工具：统一日志行为、统一异常包装。"""

from __future__ import annotations

import contextlib
import io
import sys
from typing import Iterator

from .engine import Session, SkillError, EXIT_CODES

# 用户看到的一句话建议（比原始报错有用）
HINTS: dict[str, str] = {
    "LOGIN_REQUIRED": "会话已失效：`arena cookies import <cookies 文件>` 更新登录态，或 `arena login --email 你的邮箱 --headed`",
    "LOGIN_FAILED": "邮箱被拒或验证码不对：换真实邮箱域，或直接用 cookies 文件",
    "LOGIN_TIMEOUT": "没等到登录完成：加长 --wait，或直接用 cookies 文件",
    "SEND_FAILED": "消息没发出去：稍后重试一次；连续失败用 `arena ask --headed` 观察页面",
    "TIMEOUT": "Agent 还在跑：`--timeout 1800` 加长等待，或用 `arena chat last` 拿到链接自己看",
    "UI_CHANGED": "页面结构变了：改 `~/.arena-agent/config.json` 里的 selectors，见 references/troubleshooting.md",
    "BLOCKED": "撞上 Cloudflare 人机校验：导入新鲜 cookies（含 cf_clearance），或 `--headed` 人工过一次",
    "BAD_INPUT": "输入不合法：cookies 支持 Netscape cookies.txt / EditThisCookie JSON / storage_state",
}


def exit_code(code: str) -> int:
    return EXIT_CODES.get(code, 1)


@contextlib.contextmanager
def open_session(ctx, headed: bool | None = None) -> Iterator[Session]:
    """按 Context 里的路径开一个会话；非 verbose 时吞掉引擎日志。"""
    cfg = ctx.engine_cfg
    sink: io.StringIO | contextlib.AbstractContextManager = (
        contextlib.nullcontext() if ctx.ui.verbose else contextlib.redirect_stderr(io.StringIO())
    )
    headed_flag = bool(getattr(ctx.args, "headed", False)) if ctx.args is not None else False
    with sink:
        session = Session(cfg, ctx.paths.profile_dir, ctx.paths.state_file,
                          headed=bool(headed_flag or headed))
        with session:
            yield session


def explain(code: str, message: str = "") -> str:
    return HINTS.get(code) or message or code


def handle(ctx, exc: SkillError) -> int:
    """把 SkillError 变成用户可读输出，返回退出码。"""
    ctx.ui.fail(exc.code, exc.message, explain(exc.code, exc.message))
    return exit_code(exc.code)


def require_browser(ctx) -> None:
    """早失败：playwright 没装时给出安装命令，别让用户等 30 秒才看到 ImportError。"""
    try:
        import playwright  # noqa: F401
    except ImportError:
        raise SystemExit(
            "❌ 缺少依赖：pip install playwright && python3 -m playwright install chromium"
        )
