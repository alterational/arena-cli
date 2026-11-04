"""arena status / doctor / version — overview of the environment and status."""

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
    """Offline overview (does not launch a browser; returns instantly): profile / session / configuration / recent background jobs."""
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
        "hint": ("Login credentials found in the session; use `arena check` to verify online"
                 if session.get("has_auth_cookie") else
                 "No usable login session: arena cookies import <cookies file>"),
    }

    def human() -> None:
        ctx.ui.kv([
            ("version", data["version"]),
            ("profile", data["profile"]),
            ("target site", data["base_url"]),
            ("proxy", data["proxy"] or "direct"),
            ("session file", f"{data['state_file']}"
                             + ("" if session["exists"] else " (missing)")),
            ("cookies", session.get("cookies")),
            ("login credentials", session.get("auth_cookie_names") or "none"),
            ("expired", session.get("expired_names") or "-"),
            ("config file", data["config_file"] + ("" if data["config_exists"] else " (not created)")),
            ("wait limit", f"{data['timeout']}s"),
        ], title="arena status")
        if recent:
            ctx.ui.table(["job_id", "status", "prompt"],
                         [[j["job_id"], j["status"], (j["prompt"] or "")[:48]] for j in recent],
                         title="\nRecent background jobs")
        ctx.ui.info(data["hint"])
        if not session.get("has_auth_cookie"):
            ctx.ui.info("First use: arena cookies import <cookies file> --yes   (or arena login --email your@email.com)")

    return ctx.emit(data, human=human)
