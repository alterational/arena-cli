"""arena CLI — command dispatch, global options, risk levels, structured output, exit codes.

    arena <command> [subcommand] [arguments] [global flags]

Design inspired by lark-cli:
  * The top-level help has an AGENT QUICKSTART that explains how an agent should use the tool
  * Every command is labeled with a risk level: read | write | high-risk-write; high-risk operations require --yes
  * --json output uses the {ok, identity, data, meta} envelope; errors are {ok:false, error:{type,subtype,message,hint}}
  * --jq filters JSON, --dry-run previews without executing, --no-wait runs in the background
  * arena schema <command> lets agents introspect parameters
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Optional

from . import __version__
from .config import SETTABLE, build_context
from .ui import UI

USAGE_ERROR = 64          # BSD sysexits: usage error, distinguished from business exit codes (2/3/4/5/6)

# Risk levels (aligned with lark-cli's read / write / high-risk-write)
RISK: dict[str, str] = {
    "ask": "write",
    "login": "write",
    "check": "read",
    "status": "read",
    "cookies.import": "write",
    "cookies.export": "write",
    "cookies.show": "read",
    "workspace.list": "read",
    "workspace.show": "read",
    "workspace.download": "write",
    "chat.list": "read",
    "chat.last": "read",
    "chat.open": "read",
    "config.list": "read",
    "config.show": "read",
    "config.get": "read",
    "config.path": "read",
    "config.set": "write",
    "config.unset": "write",
    "profile.list": "read",
    "profile.show": "read",
    "profile.create": "write",
    "profile.use": "write",
    "profile.delete": "high-risk-write",
    "jobs.list": "read",
    "jobs.status": "read",
    "jobs.result": "read",
    "jobs.logs": "read",
    "jobs.cancel": "write",
    "schema": "read",
    "doctor": "read",
    "version": "read",
}

EXAMPLES = """\
Examples (in recommended order):
  arena ask "Research the pricing strategies of three competitors"   # One command: ask → wait → retrieve
  arena ask --no-wait "Run a long task"                              # Run in the background; returns job_id immediately
  arena jobs result last                                             # Come back later to see the result
  arena status                                                       # Login state + environment + recent jobs
  arena schema ask                                                   # Parameters / risk / defaults for ask
  arena cookies import ~/uploads/arena.ai_cookies.txt --yes
  arena config set timeout 1800
  arena workspace list                                               # Files the Agent produced in this conversation
  arena workspace show demo.txt                                      # View contents directly
  arena workspace download demo.txt --out ./demo.txt
  arena --proxy http://127.0.0.1:7890 ask "..."                      # Route all requests through a proxy
"""

QUICKSTART = """\
arena — drive arena.ai/agent (Agent Mode) from the command line

AGENT QUICKSTART (Are you an agent? Start here):
    List commands:          arena --help
    Command details:        arena schema <command>          # Parameters, types, defaults, risk level
    Ask and get the result: arena ask "<user's original request>"   # Exit code 0 = success; stdout is the answer
    Don't block on long tasks: arena ask --no-wait "<…>"  →  arena jobs result last
    Structured output:      --json ({ok, identity, data, meta}); filter with --jq '.data.answer'
    Get produced files:     arena workspace list → arena workspace download <path> --out .
    Intranet / proxy:       --proxy http://127.0.0.1:7890 (or arena config set proxy …)
    Preview only:           --dry-run
    Risk: every command has a read | write | high-risk-write level; high-risk-write must include --yes (ask the user first)
    Failure handling: check error.subtype and error.hint (LOGIN_REQUIRED → arena cookies import)
"""


class Parser(argparse.ArgumentParser):
    """argparse uses 2 for usage errors by default, which collides with "login required = 2", so this uses 64 instead."""

    def error(self, message: str) -> "None":  # type: ignore[override]
        self.print_usage(sys.stderr)
        sys.stderr.write(f"arena: error: {message}\nTip: `arena --help` or `arena schema <command>`\n")
        raise SystemExit(USAGE_ERROR)


def _add_global(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    S = argparse.SUPPRESS
    g = p.add_argument_group("global options")
    g.add_argument("--json", action="store_true", default=S, help="output the {ok, identity, data, meta} envelope")
    g.add_argument("--format", choices=["text", "json"], default=S, help="output format")
    g.add_argument("--jq", default=S, help="filter JSON with a simplified jq expression, e.g. '.data.answer'")
    g.add_argument("-q", "--quiet", action="store_true", default=S, help="output the result only")
    g.add_argument("-v", "--verbose", action="store_true", default=S, help="show debug information (including browser logs)")
    g.add_argument("--no-color", action="store_true", default=S, help="disable colors")
    g.add_argument("--dry-run", action="store_true", default=S, help="only preview what would be done; do not execute")
    g.add_argument("--yes", action="store_true", default=S, help="confirm execution of a high-risk-write operation")
    g.add_argument("--no-wait", action="store_true", default=S, help="ask: run in the background and return job_id immediately")
    g.add_argument("--profile", default=S, help="which profile to use (default: default)")
    g.add_argument("--config", default=S, help="configuration file path (default: ~/.arena-cli/config.json)")
    g.add_argument("--base-url", default=S, help="site root URL (default: https://arena.ai)")
    g.add_argument("--timeout", type=float, default=S, help="maximum seconds to wait for the Agent to finish")
    g.add_argument("--stable", type=float, default=S, help="seconds of unchanged text required before considering it complete")
    g.add_argument("--max-chars", type=int, default=S, help="truncation length for long answers (0 = no truncation)")
    g.add_argument("--cookies", default=S,
                   help="import this cookies file first (Netscape .txt / JSON), then run the command")
    g.add_argument("--headed", action="store_true", default=S, help="show the browser window")
    g.add_argument("--proxy", default=S,
                   help="route all requests through this proxy: http://host:port / socks5://host:port / http://user:pass@host:port")
    g.add_argument("--state-file", default=S, help=argparse.SUPPRESS)
    g.add_argument("--profile-dir", default=S, help=argparse.SUPPRESS)
    return p


def _sub(sub, path: str, help_text: str, description: str = "",
         common: Optional[argparse.ArgumentParser] = None) -> argparse.ArgumentParser:
    """Dotted paths (e.g. cookies.import) denote hierarchy, but the registered command name is only the last segment."""
    name = path.split(".")[-1]
    risk = RISK.get(path)                      # Only registered leaf commands get a risk label
    epilog = (f"Risk: {risk}" + (" (requires --yes confirmation)" if risk == "high-risk-write" else "")
              ) if risk else ""
    p = sub.add_parser(name, parents=[common], help=help_text,
                       description=description or help_text,
                       epilog=epilog,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    return p


def build_parser() -> Parser:
    common = argparse.ArgumentParser(add_help=False)
    _add_global(common)

    p = Parser(
        prog="arena",
        description=QUICKSTART,
        epilog=EXAMPLES + "\nExit codes: 0 success · 1 general error · 2 login required · 3 send failed · 4 timeout · "
                          "5 page changed · 6 human verification · 64 usage error\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_global(p)
    p.add_argument("--version", action="store_true", default=argparse.SUPPRESS, help="show version")

    sub = p.add_subparsers(dest="cmd", metavar="<command>", parser_class=Parser)

    # ---- ask ----
    a = _sub(sub, "ask", "send content to the Agent and retrieve the result",
             "By default outputs the answer body; --json outputs structured results; --no-wait runs in the background.", common)
    a.add_argument("prompt", nargs="*", help="content to send; `-` or omitting it reads from stdin")
    a.add_argument("--file", help="read the prompt from a file")
    a.add_argument("--chat",
                   help="continue asking in an existing conversation (chat_id / URL / last / index from arena chat list)")
    a.add_argument("--stream", action="store_true", default=argparse.SUPPRESS,
                   help="print incremental output during generation to stderr in real time (stdout still contains only the final answer)")

    # ---- login / check / status ----
    lg = _sub(sub, "login", "log in and save cookies",
              "On first visit, clicks the terms agreement first, then clicks send to trigger the login dialog.", common)
    lg.add_argument("--email", help="email address that receives the login link / verification code")
    lg.add_argument("--code", help="one-time verification code (when the page requests it)")
    lg.add_argument("--wait", type=float, default=240, help="seconds to wait for manual login completion")
    lg.add_argument("--probe-text", default="hi", help="placeholder message used to trigger the login dialog")

    ck = _sub(sub, "check", "check whether the login session is usable", "", common)
    ck.add_argument("--probe", action="store_true", help="send a real probe message to confirm (this leaves a chat record)")
    ck.add_argument("--probe-text", default="hi")

    _sub(sub, "status", "combined status: login session + environment + recent jobs", "", common)

    # ---- cookies ----
    ck2 = _sub(sub, "cookies", "import / export / view cookies", "", common)
    csub = ck2.add_subparsers(dest="sub", metavar="<subcommand>", parser_class=Parser)
    ci = _sub(csub, "cookies.import", "import a cookies file (Netscape .txt / JSON)",
              "Overwrites the login session of the current profile; this is a high-risk operation and requires --yes.", common)
    ci.add_argument("path")
    ci.add_argument("--force", action="store_true", help="(for legacy scripts) equivalent to --yes")
    ce = _sub(csub, "cookies.export", "export the current session cookies", "", common)
    ce.add_argument("--out", help="output path (default: ~/.arena-cli/cookies.json)")
    _sub(csub, "cookies.show", "list cookies (names only, no values)", "", common)

    # ---- chat ----
    ch = _sub(sub, "chat", "conversation history", "", common)
    chsub = ch.add_subparsers(dest="sub", metavar="<subcommand>", parser_class=Parser)
    cl = _sub(chsub, "chat.list", "list recent conversations", "", common)
    cl.add_argument("--limit", type=int, default=20)
    clast = _sub(chsub, "chat.last", "print the link to the most recent conversation", "", common)
    clast.add_argument("--limit", type=int, default=5)
    co = _sub(chsub, "chat.open", "print the conversation link (can be opened in a browser)", "", common)
    co.add_argument("ref", help="chat_id / index / last / full URL")
    co.add_argument("--browser", action="store_true", help="also open it in the system browser")

    # ---- workspace ----
    wsf = _sub(sub, "workspace", "conversation workspace: list files / view contents / download",
               "View the files the Agent produced in that conversation (including uploads). Operates on the most recent conversation by default; use --chat to specify one.",
               common)
    wssub = wsf.add_subparsers(dest="sub", metavar="<subcommand>", parser_class=Parser)
    wl = _sub(wssub, "workspace.list", "list workspace files (folders are expanded automatically)", "", common)
    wl.add_argument("--chat", help="which conversation (chat_id / last / index)")
    wl.add_argument("--no-expand", action="store_true", help="list the top level only; do not expand folders")
    wsl = _sub(wssub, "workspace.show", "print the contents of a file (text)",
               "stdout contains only the file contents, suitable for direct forwarding.", common)
    wsl.add_argument("path", help="file path (as shown in workspace list; the file name alone is acceptable)")
    wsl.add_argument("--chat", help="which conversation (chat_id / last / index)")
    wd = _sub(wssub, "workspace.download", "download workspace files locally",
              "Overwriting a local file with the same name is a high-risk operation and requires --yes.", common)
    wd.add_argument("path", nargs="?", default="", help="file path (may be omitted with --all)")
    wd.add_argument("--chat", help="which conversation (chat_id / last / index)")
    wd.add_argument("--out", help="where to save (a file path or an existing directory; default: current directory)")
    wd.add_argument("--all", action="store_true", help="package the entire workspace as a zip and download it")
    wd.add_argument("--force", action="store_true", help="(for legacy scripts) equivalent to --yes")

    # ---- config ----
    cf = _sub(sub, "config", "read / write configuration", "", common)
    cfsub = cf.add_subparsers(dest="sub", metavar="<subcommand>", parser_class=Parser)
    _sub(cfsub, "config.list", "list all configuration", "", common)
    _sub(cfsub, "config.show", "same as above (alias)", "", common)
    cg = _sub(cfsub, "config.get", "read one configuration item", "", common)
    cg.add_argument("key")
    cs = _sub(cfsub, "config.set", "write one configuration item", "", common)
    cs.add_argument("key", help="available: " + ", ".join(sorted(SETTABLE)))
    cs.add_argument("value", help="value (numbers / true / false / JSON are detected automatically)")
    cu = _sub(cfsub, "config.unset", "remove a configuration item (revert to default)", "", common)
    cu.add_argument("key")
    _sub(cfsub, "config.path", "print the configuration file path", "", common)

    # ---- profile ----
    pf = _sub(sub, "profile", "switch between multiple login sessions", "", common)
    pfsub = pf.add_subparsers(dest="sub", metavar="<subcommand>", parser_class=Parser)
    _sub(pfsub, "profile.list", "list all profiles", "", common)
    _sub(pfsub, "profile.show", "details of the current profile", "", common)
    pc = _sub(pfsub, "profile.create", "create a new profile", "", common)
    pc.add_argument("name")
    pc.add_argument("--from", dest="from_cookies", help="also import the session from this cookies file")
    pu = _sub(pfsub, "profile.use", "switch the default profile", "", common)
    pu.add_argument("name")
    pd = _sub(pfsub, "profile.delete", "delete a profile (high risk; requires --yes)", "", common)
    pd.add_argument("name")

    # ---- jobs ----
    jb = _sub(sub, "jobs", "background jobs (created by ask --no-wait)", "", common)
    jsub = jb.add_subparsers(dest="sub", metavar="<subcommand>", parser_class=Parser)
    jl = _sub(jsub, "jobs.list", "list background jobs", "", common)
    jl.add_argument("--limit", type=int, default=20)
    for verb, text in (("status", "job status"), ("result", "retrieve the job result"),
                       ("logs", "view job logs"), ("cancel", "cancel the job")):
        jp = _sub(jsub, f"jobs.{verb}", text, "", common)
        jp.add_argument("ref", nargs="?", default="last", help="job id / prefix / last (default: last)")
        if verb == "logs":
            jp.add_argument("--tail", type=int, default=40)

    # ---- schema / doctor / version ----
    sc = _sub(sub, "schema", "view a command's parameters / risk / defaults (agent introspection)", "", common)
    sc.add_argument("path", nargs="*", help="command path, e.g. ask / cookies import / jobs result")
    _sub(sub, "doctor", "environment check (dependencies / browser / network / session / skill installation)", "", common)
    _sub(sub, "version", "version information", "", common)
    _sub(sub, "help", "show help", "", common)
    return p


# --------------------------------------------------------------------------- dispatch
def _dispatch(args, ctx) -> int:
    from .commands import (admin, ask as ask_cmd, auth, chat, config_cmd, cookies, jobs_cmd,
                           profile, schema_cmd, workspace as workspace_cmd)

    key = (args.cmd, getattr(args, "sub", None))
    table: dict[tuple[str, Optional[str]], Callable[[Any, Any], int]] = {
        ("ask", None): ask_cmd.run,
        ("login", None): auth.run_login,
        ("check", None): auth.run_check,
        ("status", None): admin.run_status,
        ("cookies", "import"): cookies.run_import,
        ("cookies", "export"): cookies.run_export,
        ("cookies", "show"): cookies.run_show,
        ("workspace", "list"): workspace_cmd.run_list,
        ("workspace", "show"): workspace_cmd.run_show,
        ("workspace", "download"): workspace_cmd.run_download,
        ("chat", "list"): chat.run_list,
        ("chat", "last"): chat.run_last,
        ("chat", "open"): chat.run_open,
        ("config", "list"): config_cmd.run_list,
        ("config", "show"): config_cmd.run_list,
        ("config", "get"): config_cmd.run_get,
        ("config", "set"): config_cmd.run_set,
        ("config", "unset"): config_cmd.run_unset,
        ("config", "path"): config_cmd.run_path,
        ("profile", "list"): profile.run_list,
        ("profile", "show"): profile.run_show,
        ("profile", "create"): profile.run_create,
        ("profile", "use"): profile.run_use,
        ("profile", "delete"): profile.run_delete,
        ("jobs", "list"): jobs_cmd.run_list,
        ("jobs", "status"): jobs_cmd.run_status,
        ("jobs", "result"): jobs_cmd.run_result,
        ("jobs", "logs"): jobs_cmd.run_logs,
        ("jobs", "cancel"): jobs_cmd.run_cancel,
        ("schema", None): schema_cmd.run,
        ("doctor", None): admin.run_doctor,
        ("version", None): admin.run_version,
    }
    handler = table.get(key)
    if handler is None:
        from .errors import unknown_command
        raise unknown_command(
            f"{args.cmd} {getattr(args, 'sub', '') or ''}".strip(),
            sorted({k[0] for k in table} | {"schema", "jobs", "status"}))
    return handler(ctx, args)


def _risk_of(args) -> str:
    key = f"{args.cmd}.{getattr(args, 'sub', '')}" if getattr(args, "sub", None) else args.cmd
    return RISK.get(key, "read")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "version", False):
        print(f"arena {__version__}")
        return 0
    if not getattr(args, "cmd", None) or args.cmd == "help":
        parser.print_help()
        return 0

    fmt = getattr(args, "format", None)
    json_mode = bool(getattr(args, "json", False) or fmt == "json")
    ui = UI(
        json_mode=json_mode,
        color=False if getattr(args, "no_color", False) else None,
        quiet=bool(getattr(args, "quiet", False)),
        verbose=bool(getattr(args, "verbose", False)),
    )
    ctx = build_context(args, ui)

    from .errors import ArenaError, confirmation, from_skill_error
    from .engine import SkillError, cmd_import
    from pathlib import Path

    def fail(err: ArenaError) -> int:
        if ui.json_mode or getattr(args, "jq", None):
            print(json.dumps({"ok": False, "identity": ctx.paths.profile,
                              "error": err.to_dict()}, ensure_ascii=False, indent=2))
        else:
            ui.err(f"{err.type}.{err.subtype}: {err.message}")
            if err.hint:
                print(f"  {ui.paint('→ ' + err.hint, 'grey')}", file=sys.stderr)
        return err.exit_code

    try:
        # Global --cookies: import first (high risk; requires --yes)
        cookies_file = getattr(args, "cookies", None)
        if cookies_file:
            target = Path(cookies_file).expanduser()
            if not getattr(args, "yes", False) and not getattr(args, "dry_run", False):
                raise confirmation(f"overwrite the login session of profile \"{ctx.paths.profile}\" with {target.name}",
                                   "add --yes to confirm; or use --dry-run first to see what would be imported")
            imp = cmd_import(target, ctx.paths.state_file, ctx.engine_cfg)
            ui.debug(f"imported {imp['count']} cookies (login credentials: "
                     f"{', '.join(imp['auth_cookie_names']) or 'not recognized'})")
            if not imp["has_auth_cookie"]:
                ui.warn("No login credential was recognized in these cookies; you may still need arena login")

        # Risk gate
        risk = _risk_of(args)
        needs_confirm = (risk == "high-risk-write"
                         and not getattr(args, "yes", False)
                         and not getattr(args, "dry_run", False))
        if needs_confirm:
            raise confirmation(f"arena {args.cmd} {getattr(args, 'sub', '') or ''}".strip())

        return _dispatch(args, ctx)
    except ArenaError as err:
        return fail(err)
    except SkillError as exc:
        return fail(from_skill_error(exc))
    except SystemExit:
        raise
    except FileNotFoundError as exc:
        return fail(ArenaError("validation", "not_found", str(exc), "arena jobs list"))
    except KeyboardInterrupt:
        ui.err("Interrupted")
        return 130
    except BrokenPipeError:
        return 0
    except Exception as exc:  # noqa: BLE001
        from playwright.sync_api import Error as PWError
        if isinstance(exc, PWError):
            return fail(ArenaError("runtime", "browser_error", str(exc).splitlines()[0],
                                   "use --headed to observe the page, or run arena doctor"))
        if ui.verbose:
            import traceback
            traceback.print_exc()
        return fail(ArenaError("runtime", "internal", f"{type(exc).__name__}: {exc}",
                               "add -v for the full stack trace; or run arena doctor"))


if __name__ == "__main__":
    sys.exit(main())
