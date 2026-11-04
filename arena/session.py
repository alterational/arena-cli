"""Helpers for opening browser sessions: unified logging behavior and unified exception wrapping."""

from __future__ import annotations

import contextlib
import io
import sys
from typing import Iterator

from .engine import Session, SkillError, EXIT_CODES

# One-line suggestions shown to users (more useful than raw errors)
HINTS: dict[str, str] = {
    "LOGIN_REQUIRED": "Session has expired: run `arena cookies import <cookies file>` to update the login session, or `arena login --email your@email.com --headed`",
    "LOGIN_FAILED": "Email was rejected or the verification code is wrong: use a real email domain, or use a cookies file directly",
    "LOGIN_TIMEOUT": "Login did not complete in time: increase --wait, or use a cookies file directly",
    "SEND_FAILED": "Message was not sent: retry once later; if it keeps failing, use `arena ask --headed` to observe the page",
    "TIMEOUT": "The Agent is still running: use `--timeout 1800` to wait longer, or get the link with `arena chat last` and check it yourself",
    "UI_CHANGED": "Page structure changed: update the selectors in `~/.arena-cli/config.json`; see references/troubleshooting.md",
    "BLOCKED": "Hit Cloudflare human verification: import fresh cookies (including cf_clearance), or pass the check manually once with `--headed`",
    "BAD_INPUT": "Invalid input: cookies may be in Netscape cookies.txt / EditThisCookie JSON / storage_state format",
}


def exit_code(code: str) -> int:
    return EXIT_CODES.get(code, 1)


@contextlib.contextmanager
def open_session(ctx, headed: bool | None = None) -> Iterator[Session]:
    """Open a session using the paths in the Context; in non-verbose mode, swallow the engine logs."""
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
    """Turn a SkillError into user-readable output and return the exit code."""
    ctx.ui.fail(exc.code, exc.message, explain(exc.code, exc.message))
    return exit_code(exc.code)


def require_browser(ctx) -> None:
    """Fail early: if playwright is not installed, print the install command so users do not wait 30 seconds before seeing an ImportError."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        raise SystemExit(
            "❌ Missing dependency: pip install playwright && python3 -m playwright install chromium"
        )
