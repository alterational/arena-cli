"""arena cookies import|export|show — session (cookies) management."""

from __future__ import annotations

import time
from pathlib import Path

from ..engine import (cmd_export, cmd_import, parse_cookie_file, summarize_cookies)
from ..errors import confirmation
from ..session import open_session


def _existing_session(ctx) -> dict:
    path = ctx.paths.state_file
    if not path.exists():
        return {"exists": False, "has_auth_cookie": False}
    try:
        cookies, _ = parse_cookie_file(path)
    except Exception:  # noqa: BLE001
        return {"exists": True, "has_auth_cookie": False}
    health = summarize_cookies(cookies, ctx.engine_cfg)
    return {"exists": True, **health}


def run_import(ctx, args) -> int:
    src = Path(args.path).expanduser()
    if not src.exists():
        from ..errors import ArenaError
        raise ArenaError("validation", "not_found", f"File does not exist: {src}",
                         "Check the path, or ask the user to upload the cookies file again", exit_code=1)

    current = _existing_session(ctx)
    would_overwrite = bool(current.get("has_auth_cookie"))

    # ---- dry-run: parse the file only; do not write to disk ----
    if ctx.dry_run():
        cookies, meta = parse_cookie_file(src)
        health = summarize_cookies(cookies, ctx.engine_cfg)
        data = {"source": str(src), "format": meta.get("format"), **health,
                "would_overwrite": would_overwrite,
                "target": str(ctx.paths.state_file)}
        return ctx.emit(data, dry_run=True, human=lambda: (
            ctx.ui.kv([("source", data["source"]), ("format", data["format"]),
                       ("count", data["count"]), ("domains", data["domains"]),
                       ("login credentials", data["auth_cookie_names"] or "not recognized"),
                       ("overwrites existing session", "yes" if would_overwrite else "no")],
                      title="[dry-run] arena cookies import"),
            ctx.ui.info("To import for real, drop --dry-run (overwriting a login session requires --yes)"),
        ))

    # Overwriting a healthy login session = high risk: explicit confirmation required
    if would_overwrite and not getattr(args, "yes", False) and not getattr(args, "force", False):
        raise confirmation(
            f"overwrite the existing login session of profile \"{ctx.paths.profile}\" ({len(current.get('auth_cookie_names') or [])} credential cookies)",
            "add --yes to confirm; or use --profile <name> --cookies <file> to import into another profile")

    res = cmd_import(src, ctx.paths.state_file, ctx.engine_cfg)
    data = {**res, "profile": ctx.paths.profile, "overwrote": would_overwrite}

    def human() -> None:
        ctx.ui.ok(f"Imported {res['count']} cookies → {res['state_file']}")
        ctx.ui.kv([("source", res["source"]), ("format", res["format"]), ("domains", res["domains"]),
                   ("login credentials", res["auth_cookie_names"] or "not recognized"),
                   ("expired", res["expired_names"] or "-"), ("profile", ctx.paths.profile)])
        if res.get("warning"):
            ctx.ui.warn(res["warning"])
        if res["has_auth_cookie"]:
            ctx.ui.info('Next step: arena check, or directly arena ask "your question"')
        else:
            ctx.ui.warn("No login credential cookie was found: this file may not contain a login session")

    return ctx.emit(data, human=human)


def run_export(ctx, args) -> int:
    out = Path(args.out).expanduser() if args.out else ctx.paths.home / "cookies.json"
    if ctx.dry_run():
        return ctx.emit({"action": "export", "target": str(out)}, dry_run=True,
                        human=lambda: ctx.ui.info(f"[dry-run] would export the current session to {out}"))
    with open_session(ctx) as session:
        res = cmd_export(session, out)
    return ctx.emit(res, human=lambda: (
        ctx.ui.ok(f"Exported {res['cookies']} cookies → {res['exported']}"),
        ctx.ui.warn("This file is equivalent to your account password; do not share it or commit it to the repository"),
    ))


def run_show(ctx, args) -> int:
    path = ctx.paths.state_file
    if not path.exists():
        from ..errors import of
        raise of("LOGIN_REQUIRED", f"No session file: {path}")
    cookies, _ = parse_cookie_file(path)
    health = summarize_cookies(cookies, ctx.engine_cfg)
    now = time.time()
    rows = []
    for c in sorted(cookies, key=lambda c: c["name"]):
        exp = c.get("expires", -1)
        if exp in (-1, 0):
            exp_s = "session"
        else:
            exp_s = time.strftime("%Y-%m-%d", time.localtime(exp))
            if exp < now:
                exp_s += " (expired)"
        rows.append([c["name"], c.get("domain", ""), exp_s,
                     "httpOnly" if c.get("httpOnly") else ""])

    data = {"state_file": str(path), "profile": ctx.paths.profile, **health,
            "cookies": [{"name": c["name"], "domain": c.get("domain"),
                         "expires": c.get("expires")} for c in cookies]}

    def human() -> None:
        ctx.ui.kv([("profile", ctx.paths.profile), ("state_file", str(path)),
                   ("cookies", health["count"]),
                   ("login credentials", health["auth_cookie_names"] or "not recognized"),
                   ("expired", health["expired_names"] or "-")])
        ctx.ui.table(["name", "domain", "expires", "flag"], rows, title="\ncookies (values not shown)")

    ctx.emit(data, human=human)
    return 0 if health["has_auth_cookie"] else 2
