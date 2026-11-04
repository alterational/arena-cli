"""Unified error model (aligned with lark-cli's error.type / error.subtype / error.hint)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Engine error code -> (type, subtype, exit code, default hint)
CODE_MAP: dict[str, tuple[str, str, int, str]] = {
    "LOGIN_REQUIRED": ("auth", "login_required", 2,
                       "arena cookies import <cookies file>, or arena login --email your@email.com --headed"),
    "LOGIN_FAILED": ("auth", "login_failed", 2, "Use a real email domain, or use a cookies file directly"),
    "LOGIN_TIMEOUT": ("auth", "login_timeout", 2, "Increase --wait, or use a cookies file directly"),
    "SEND_FAILED": ("runtime", "send_failed", 3, "Retry once; if it still fails, use --headed to observe the page"),
    "TIMEOUT": ("timeout", "agent_timeout", 4, "Add --timeout; or run in the background with arena jobs"),
    "UI_CHANGED": ("runtime", "ui_changed", 5,
                   "Update the selectors with arena config set selectors '{...}' (see references/troubleshooting.md)"),
    "RATE_LIMITED": ("rate_limit", "quota_exceeded", 7,
                     "Arena returned 429 (too many requests or quota exhausted): wait a few minutes and retry, or use different cookies (arena profile create <name> --from <cookies>)"),
    "BLOCKED": ("network", "bot_challenge", 6,
                "Import fresh cookies (including cf_clearance), or pass the check manually once with --headed"),
    "BAD_INPUT": ("validation", "invalid_argument", 1,
                  "Cookies may be in Netscape cookies.txt / EditThisCookie JSON / storage_state format"),
    "DEPENDENCY_MISSING": ("config", "dependency_missing", 1,
                           "pip install playwright && python3 -m playwright install chromium"),
    "BROWSER_ERROR": ("runtime", "browser_error", 1,
                      "Run arena doctor to check the environment; if --proxy is used, first confirm the proxy works (curl -x <proxy> https://arena.ai)"),
    "NOT_FOUND": ("validation", "not_found", 1, "Run arena chat list to see what exists"),
    "CONFIRMATION_REQUIRED": ("usage", "confirmation_required", 1,
                              "This is a high-risk-write operation; add --yes to confirm"),
    "UNKNOWN_COMMAND": ("usage", "unknown_command", 64, "Run arena schema to see the available commands"),
}


@dataclass
class ArenaError(Exception):
    """Unified shape of all external errors."""

    type: str
    subtype: str
    message: str
    hint: str = ""
    exit_code: int = 1
    params: list[dict[str, Any]] = field(default_factory=list)

    def __str__(self) -> str:  # for logging
        return f"{self.type}.{self.subtype}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        err: dict[str, Any] = {"type": self.type, "subtype": self.subtype, "message": self.message}
        if self.hint:
            err["hint"] = self.hint
        if self.params:
            err["params"] = self.params
        return err


def of(code: str, message: str = "", hint: Optional[str] = None,
       params: Optional[list[dict[str, Any]]] = None) -> ArenaError:
    """Build an ArenaError from a business error code."""
    etype, subtype, exit_code, default_hint = CODE_MAP.get(
        code, ("runtime", code.lower(), 1, ""))
    return ArenaError(etype, subtype, message or code, hint or default_hint,
                      exit_code, params or [])


def from_skill_error(exc: Any) -> ArenaError:
    return of(getattr(exc, "code", "INTERNAL"), getattr(exc, "message", str(exc)))


def confirmation(action: str, hint: str = "") -> ArenaError:
    return of("CONFIRMATION_REQUIRED",
              f"{action} is a high-risk-write operation",
              hint or "Add --yes and run again to confirm")


def unknown_command(name: str, available: list[str]) -> ArenaError:
    return of("UNKNOWN_COMMAND", f"Unknown command: {name}", "Available commands: " + ", ".join(available))
