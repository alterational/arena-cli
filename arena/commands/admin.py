"""arena status / doctor / version —— 环境与状态总览。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..engine import parse_cookie_file, summarize_cookies
from . import doctor as doctor_cmd

run_doctor = doctor_cmd.run_doctor
run_version = doctor_cmd.run_version


def _session_summary(state_file: Path, engine_cfg: dict) -> dict:
    if not state_file.exists():
        return {"exists": False, "cookies": 0, "has_auth_cookie": False,
                "auth_cookie_names": [], "expired_names": []}
    try:
        cookies, _ = parse_cookie_file(state_file)
    except Exception as exc:  # noqa: BLE001
        return {"exists": True, "error": str(exc), "cookies": 0, "has_auth_cookie": False}
    health = summarize_cookies(cookies, engine_cfg)
    return {"exists": True, **health}


def run_status(ctx, args) -> int:
    """离线总览（不启动浏览器，秒回）：profile / 会话 / 配置 / 最近后台任务。"""
    from .. import __version__, jobs

    session = _session_summary(ctx.paths.state_file, ctx.engine_cfg)
    recent = jobs.list_jobs(ctx.paths.home, limit=3)

    from ..engine import mask_proxy

    data = {
        "version": __version__,
        "profile": ctx.paths.profile,
        "base_url": ctx.get("base_url"),
        "proxy": mask_proxy(str(ctx.get("proxy") or "")) or None,
        "state_file": str(ctx.paths.state_file),
        "session": session,
        "config_file": str(ctx.paths.config_file),
        "config_exists": ctx.paths.config_file.exists(),
        "timeout": ctx.get("timeout"),
        "recent_jobs": [{k: j[k] for k in ("job_id", "status", "prompt")} for j in recent],
        "hint": ("会话里发现登录凭证；用 `arena check` 做线上验证"
                 if session.get("has_auth_cookie") else
                 "没有可用登录态：arena cookies import <cookies 文件>"),
    }

    def human() -> None:
        ctx.ui.kv([
            ("版本", data["version"]),
            ("profile", data["profile"]),
            ("目标站点", data["base_url"]),
            ("代理", data["proxy"] or "直连"),
            ("会话文件", f"{data['state_file']}"
                        + ("" if session["exists"] else "（不存在）")),
            ("cookies", session.get("cookies")),
            ("登录凭证", session.get("auth_cookie_names") or "无"),
            ("已过期", session.get("expired_names") or "-"),
            ("配置文件", data["config_file"] + ("" if data["config_exists"] else "（未创建）")),
            ("等待上限", f"{data['timeout']}s"),
        ], title="arena status")
        if recent:
            ctx.ui.table(["job_id", "状态", "prompt"],
                         [[j["job_id"], j["status"], (j["prompt"] or "")[:48]] for j in recent],
                         title="\n最近后台任务")
        ctx.ui.info(data["hint"])
        if not session.get("has_auth_cookie"):
            ctx.ui.info("首次使用：arena cookies import <cookies 文件> --yes   （或 arena login --email 你的邮箱）")

    return ctx.emit(data, human=human)
