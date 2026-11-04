"""arena login / arena check — login and login session health check."""

from __future__ import annotations

from ..engine import cmd_check, cmd_login
from ..session import open_session


def run_login(ctx, args) -> int:
    if ctx.dry_run():
        return ctx.emit({"action": "login", "profile": ctx.paths.profile,
                         "email": args.email, "wait": args.wait,
                         "state_file": str(ctx.paths.state_file)},
                        dry_run=True,
                        human=lambda: ctx.ui.kv([
                            ("action", "login (not actually executed)"),
                            ("profile", ctx.paths.profile),
                            ("email", args.email or "(not provided; complete it manually in the window)"),
                            ("wait", f"{args.wait}s"),
                        ], title="[dry-run] arena login"))

    ctx.ui.debug(f"login profile={ctx.paths.profile}")
    if not args.email:
        ctx.ui.info("No --email given: a login dialog will open, and you complete the login in the window")
    with open_session(ctx) as session:
        res = cmd_login(session, args.email, args.code, args.wait, args.probe_text)

    state = "Already up to date with the current login session" if res.get("already_logged_in") else "Login successful"
    data = {**res, "profile": ctx.paths.profile}
    return ctx.emit(data, human=lambda: (
        ctx.ui.ok(f"{state}; session saved: {res.get('state_file')}"),
        ctx.ui.kv([("profile", ctx.paths.profile), ("cookies", res.get("cookies")),
                   ("state_file", res.get("state_file"))]),
        ctx.ui.info('Next step: arena ask "your question"'),
    ))


def run_check(ctx, args) -> int:
    with open_session(ctx) as session:
        res = cmd_check(session, probe=args.probe, probe_text=args.probe_text)

    logged_in = res.get("logged_in")
    label = {True: "Logged in", False: "Not logged in", None: "Cannot confirm"}[logged_in]

    def human() -> None:
        (ctx.ui.ok if logged_in else ctx.ui.warn)(f"{label} · profile \"{ctx.paths.profile}\"")
        ctx.ui.kv([("state_file", res.get("state_file")), ("cookies", res.get("cookies")),
                   ("login credentials", res.get("auth_cookie_names")), ("page", res.get("url"))])
        if res.get("hint"):
            ctx.ui.info(res["hint"])

    ctx.emit({**res, "profile": ctx.paths.profile}, human=human)
    # For scripts: definitely not logged in -> 2
    return 2 if logged_in is False else 0
