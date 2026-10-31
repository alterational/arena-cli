"""统一错误模型（对齐 lark-cli 的 error.type / error.subtype / error.hint）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# 引擎错误码 -> (type, subtype, 退出码, 默认 hint)
CODE_MAP: dict[str, tuple[str, str, int, str]] = {
    "LOGIN_REQUIRED": ("auth", "login_required", 2,
                       "arena cookies import <cookies 文件>，或 arena login --email 你的邮箱 --headed"),
    "LOGIN_FAILED": ("auth", "login_failed", 2, "换真实邮箱域，或直接用 cookies 文件"),
    "LOGIN_TIMEOUT": ("auth", "login_timeout", 2, "加长 --wait，或直接用 cookies 文件"),
    "SEND_FAILED": ("runtime", "send_failed", 3, "重试一次；仍失败用 --headed 观察页面"),
    "TIMEOUT": ("timeout", "agent_timeout", 4, "加 --timeout；或用 arena jobs 后台跑"),
    "UI_CHANGED": ("runtime", "ui_changed", 5,
                   "arena config set selectors '{...}' 更新选择器（见 references/troubleshooting.md）"),
    "RATE_LIMITED": ("rate_limit", "quota_exceeded", 7,
                     "Arena 返回 429（请求过多/额度用尽）：等几分钟再试，或换一份 cookies（arena profile create <名> --from <cookies>）"),
    "BLOCKED": ("network", "bot_challenge", 6,
                "导入新鲜 cookies（含 cf_clearance），或 --headed 人工过一次"),
    "BAD_INPUT": ("validation", "invalid_argument", 1,
                  "cookies 支持 Netscape cookies.txt / EditThisCookie JSON / storage_state"),
    "DEPENDENCY_MISSING": ("config", "dependency_missing", 1,
                           "pip install playwright && python3 -m playwright install chromium"),
    "BROWSER_ERROR": ("runtime", "browser_error", 1,
                      "arena doctor 体检环境；用了 --proxy 时先确认代理可用（curl -x <proxy> https://arena.ai）"),
    "NOT_FOUND": ("validation", "not_found", 1, "arena chat list 看看有哪些"),
    "CONFIRMATION_REQUIRED": ("usage", "confirmation_required", 1,
                              "这是 high-risk-write 操作，确认后加 --yes"),
    "UNKNOWN_COMMAND": ("usage", "unknown_command", 64, "arena schema 查看可用命令"),
}


@dataclass
class ArenaError(Exception):
    """所有对外错误的统一形状。"""

    type: str
    subtype: str
    message: str
    hint: str = ""
    exit_code: int = 1
    params: list[dict[str, Any]] = field(default_factory=list)

    def __str__(self) -> str:  # 便于日志
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
    """按业务错误码构造 ArenaError。"""
    etype, subtype, exit_code, default_hint = CODE_MAP.get(
        code, ("runtime", code.lower(), 1, ""))
    return ArenaError(etype, subtype, message or code, hint or default_hint,
                      exit_code, params or [])


def from_skill_error(exc: Any) -> ArenaError:
    return of(getattr(exc, "code", "INTERNAL"), getattr(exc, "message", str(exc)))


def confirmation(action: str, hint: str = "") -> ArenaError:
    return of("CONFIRMATION_REQUIRED",
              f"{action} 是 high-risk-write 操作",
              hint or "确认后加 --yes 重跑")


def unknown_command(name: str, available: list[str]) -> ArenaError:
    return of("UNKNOWN_COMMAND", f"未知命令：{name}", "可用命令：" + ", ".join(available))
