#!/usr/bin/env python3
"""
run_cli_tests.py —— verifies the `arena` CLI (modeled on the lark-cli system)

Covers:
  1. Top level: --help / version / python -m arena / bin/arena
  2. Usage errors → 64
  3. config: set / get / list / show / unset / path (types restored automatically) + global flags before and after the command
  4. cookies: import (first time) / show / dry-run
  5. check / status
  6. ask: body / envelope / jq / max-chars / stdin / --file
  7. chat: list / last / open
  8. profile: create / list / use / delete (high risk; requires --yes)
  9. doctor
 10. Envelope structure {ok, identity, data, meta} and error {ok:false, error:{type,subtype,message,hint}}
 11. --dry-run: ask / cookies import / profile delete must not actually execute
 12. Risk levels: a cookies import that overwrites the login session requires --yes
 13. --no-wait + jobs: list / status / result / logs / cancel
 14. schema: list / single command / unknown command
 15. workspace: list / show / download / --all / overwrite guard
 16. --proxy: really goes through the proxy (flag / config file)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "arena"
sys.path.insert(0, str(ROOT / "tests"))
import mock_server  # noqa: E402
import proxy_server  # noqa: E402

FAILED: list[str] = []
HOME = Path(tempfile.mkdtemp(prefix="arena-cli-home-"))


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILED.append(name)


def run(*args: str, stdin: str | None = None, timeout: int = 180):
    env = {**os.environ, "ARENA_HOME": str(HOME), "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
    return subprocess.run([str(BIN), *args], capture_output=True, text=True,
                          input=stdin, timeout=timeout, env=env)


def as_json(proc=None, **kwargs) -> dict:
    if proc is None:                    # call style such as as_json("--json", "status", timeout=300)
        proc = run(*kwargs.pop("args", ()), **kwargs)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def main() -> int:
    srv = mock_server.serve(0)
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"mock server: {base}/agent   HOME={HOME}\n")
    g = ["--base-url", base]

    # ---------- 1. Top level ----------
    print("[1] Top-level commands")
    r = run("--help")
    check("--help exit code 0", r.returncode == 0)
    check("help contains AGENT QUICKSTART", "AGENT QUICKSTART" in r.stdout, r.stdout[:200])
    check("help lists all commands",
          all(c in r.stdout for c in ("ask", "login", "check", "status", "cookies", "chat",
                                      "config", "profile", "jobs", "schema", "doctor")), "")
    check("version works", run("version").stdout.strip().startswith("arena 2."),
          run("version").stdout[:80])
    r = subprocess.run([sys.executable, "-m", "arena", "version"], cwd=ROOT,
                       capture_output=True, text=True, env={**os.environ, "ARENA_HOME": str(HOME)})
    check("python3 -m arena works", r.returncode == 0 and r.stdout.startswith("arena 2."), r.stdout[:80])

    # ---------- 2. Usage errors ----------
    print("\n[2] Usage errors → 64")
    check("missing argument = 64", run("config", "get").returncode == 64)
    check("unknown flag = 64", run("ask", "--nope").returncode == 64)
    check("unknown subcommand = 64", run("cookies", "bogus").returncode == 64)

    # ---------- 3. config ----------
    print("\n[3] arena config")
    check("set succeeded", run("config", "set", "timeout", "1800").returncode == 0)
    check("get returns 1800", run("config", "get", "timeout").stdout.strip() == "1800",
          run("config", "get", "timeout").stdout)
    run("config", "set", "base_url", base)
    check("get returns a string", base in run("config", "get", "base_url").stdout)
    run("config", "set", "color", "false")
    d = as_json(run("--json", "config", "get", "color"))
    check("type auto-restored false → bool", d.get("data", {}).get("color") is False, str(d)[:200])
    r = run("config", "list")
    check("list shows effective values", "1800" in r.stdout and "profile" in r.stdout, r.stdout[:200])
    check("show is an alias of list", run("config", "show").returncode == 0)
    check("path points to ARENA_HOME", str(HOME / "config.json") in run("config", "path").stdout)
    check("unknown config key is rejected", run("config", "set", "nope", "1").returncode == 1)
    check("unset succeeded", run("config", "unset", "color").returncode == 0)

    # ---------- 4. cookies ----------
    print("\n[4] arena cookies")
    ns = HOME / "up.txt"
    ns.write_text("# Netscape HTTP Cookie File\n\n"
                  "127.0.0.1\tFALSE\t/\tFALSE\t4102444800\tmock_session\t1\n"
                  "127.0.0.1\tFALSE\t/\tFALSE\t4102444800\tarena-auth-prod-v1.1\tabcdef\n",
                  encoding="utf-8")

    print("  · dry-run must not write to disk")
    r = run(*g, "--dry-run", "cookies", "import", str(ns))
    d = as_json(run(*g, "--json", "--dry-run", "cookies", "import", str(ns)))
    check("dry-run flag present", d.get("dry_run") is True, str(d)[:200])
    check("dry-run reports would_overwrite = no", d.get("data", {}).get("would_overwrite") is False, str(d)[:200])
    check("dry-run did not create a state file", not (HOME / "state.json").exists())

    r = run(*g, "cookies", "import", str(ns))
    check("first import succeeded (no --yes needed)", r.returncode == 0, r.stderr[-200:])
    check("login credentials reported", "arena-auth-prod-v1.1" in r.stdout, r.stdout[:200])
    d = as_json(run(*g, "--json", "cookies", "show"))
    check("show --json gives names only, not values", "abcdef" not in json.dumps(d), str(d)[:200])

    # ---------- 5. check / status ----------
    print("\n[5] check / status")
    r = run(*g, "check")
    check("logged in → exit code 0", r.returncode == 0, f"{r.returncode}: {r.stdout[:200]}")
    d = as_json(run(*g, "--json", "check"))
    check("envelope has logged_in", d.get("data", {}).get("logged_in") is True, str(d)[:200])
    d = as_json(run(*g, "--json", "status"))
    check("status shows a session overview", d.get("data", {}).get("session", {}).get("has_auth_cookie") is True,
          str(d)[:200])

    # ---------- 6. ask ----------
    print("\n[6] arena ask")
    r = run(*g, "ask", "Summarize three to-dos")
    check("body output", r.returncode == 0 and r.stdout.strip().startswith("ECHO["), r.stdout[:160])
    check("stdout is clean", "[arena]" not in r.stdout and not r.stdout.strip().startswith("{"), "")
    d = as_json(run(*g, "--json", "ask", "Structure this"))
    check("envelope structure ok/identity/data/meta",
          d.get("ok") is True and d.get("identity") == "default"
          and isinstance(d.get("data"), dict) and "version" in d.get("meta", {}), str(d)[:240])
    check("data contains answer/url",
          "answer" in d.get("data", {}) and "url" in d.get("data", {}), str(d.get("data"))[:200])
    check("global flag also works after the command", as_json(run(*g, "ask", "after", "--json")).get("ok") is True)
    out = run(*g, "--jq", ".data.answer", "ask", "jq filter").stdout.strip()
    check("--jq extracts the value", out.startswith("ECHO[") and "jq filter" in out, out[:120])
    r = run(*g, "ask", "truncation test " * 40, "--max-chars", "60")
    check("--max-chars truncates and appends a link", "truncated" in r.stdout and "http" in r.stdout, r.stdout[-120:])
    check("stdin works", "from stdin" in run(*g, "ask", "-", stdin="from stdin").stdout)
    qf = HOME / "q.md"
    qf.write_text("Question from file", encoding="utf-8")
    check("--file works", "Question from file" in run(*g, "ask", "--file", str(qf)).stdout)

    # Multi-turn: --chat selects the session (three forms: last / index / bare id)
    d = as_json(run(*g, "--json", "ask", "--chat", "last", "continue to round two"))
    check("--chat last continues the session", d.get("ok") is True, str(d)[:200])
    check("--chat last lands on the real chat page",
          "chat-aaaaaaaa" in str(d.get("data", {}).get("url", "")),
          str(d.get("data", {}).get("url")))
    d2 = as_json(run(*g, "--json", "ask", "--chat", "1", "index also works"))
    check("--chat index continues the session", d2.get("ok") is True
          and "chat-aaaaaaaa" in str(d2.get("data", {}).get("url", "")), str(d2)[:200])
    d3 = as_json(run(*g, "--json", "ask", "--chat", "chat-bbbbbbbb-0002", "bare id"))
    check("--chat bare id continues the session", d3.get("ok") is True
          and "chat-bbbbbbbb-0002" in str(d3.get("data", {}).get("url", "")), str(d3)[:200])
    d4 = as_json(run(*g, "--json", "ask", "--chat", "1", "dryrun", "--dry-run"))
    check("--dry-run shows the session target", str(d4.get("data", {}).get("chat", "")).startswith("1")
          or d4.get("dry_run") is True, str(d4)[:160])

    # Streaming: stderr carries intermediate progress; stdout still contains only the final answer
    r = run(*g, "ask", "--stream", "streaming output test")
    check("--stream exit code 0", r.returncode == 0, r.stderr[-160:])
    check("--stream stderr has incremental output", "ECHO[" in r.stderr, repr(r.stderr[-120:]))
    check("--stream increments joined together form the full answer", r.stderr.strip() == r.stdout.strip(),
          f"stdout={r.stdout[:60]!r} stderr={r.stderr[:60]!r}")
    check("--stream stdout still contains only the answer body",
          r.stdout.strip().startswith("ECHO[") and "{" not in r.stdout[:2], r.stdout[:120])
    check("without the flag, stderr has no body", "ECHO[" not in run(*g, "ask", "quiet mode").stderr)
    check("-q disables streaming", "ECHO[" not in run(*g, "-q", "ask", "--stream", "quiet streaming").stderr)
    d = as_json(run(*g, "--json", "ask", "--stream", "streaming+json"))
    check("--stream and --json can coexist", d.get("ok") is True
          and d.get("data", {}).get("answer", "").startswith("ECHO["), str(d)[:160])

    # ---------- 7. chat ----------
    print("\n[7] arena chat")
    check("list reads history", "Ask with the uploaded cookies" in run(*g, "chat", "list").stdout)
    d = as_json(run(*g, "--json", "chat", "list"))
    check("list --json has a chats array", isinstance(d.get("data", {}).get("chats"), list), str(d)[:160])
    check("--jq extracts titles", "Ask with the uploaded cookies" in run(*g, "--jq", ".data.chats[].title",
                                                     "chat", "list").stdout)
    r = run(*g, "chat", "last")
    check("last outputs the URL", r.stdout.strip().startswith(base) and "/agent/chat-" in r.stdout, r.stdout)
    check("open index 2 is correct", "chat-bbbbbbbb-0002" in run(*g, "chat", "open", "2").stdout)

    # ---------- 8. profile ----------
    print("\n[8] arena profile")
    check("list contains default", "default" in run(*g, "profile", "list").stdout)
    check("create --from cookies", run(*g, "profile", "create", "work", "--from", str(ns)).returncode == 0)
    check("work session is independent", (HOME / "profiles" / "work" / "state.json").exists())
    check("use succeeded", run("profile", "use", "work").returncode == 0)
    d = as_json(run("--json", "profile", "show"))
    check("current profile = work", d.get("data", {}).get("profile") == "work", str(d)[:160])
    check("work can also ask", "in work" in run(*g, "ask", "asking in work profile").stdout)

    print("  · high-risk operations require --yes")
    r = run("profile", "delete", "work")
    check("exit code 1 without --yes", r.returncode == 1, f"{r.returncode}")
    check("reports confirmation_required", "confirmation_required" in (r.stdout + r.stderr), r.stderr[-160:])
    d = as_json(run("--json", "profile", "delete", "work"))
    check("error envelope type=usage",
          d.get("ok") is False and d.get("error", {}).get("type") == "usage", str(d)[:200])
    check("delete --yes succeeded", run("profile", "delete", "work", "--yes").returncode == 0)
    check("profile directory cleared", not (HOME / "profiles" / "work").exists())

    # ---------- 9. doctor ----------
    print("\n[9] arena doctor")
    d = as_json(run("--json", "doctor"))
    names = [c["name"] for c in d.get("data", {}).get("checks", [])]
    check("doctor --json has structure", bool(names), str(d)[:200])
    check("contains key checks", all(n in names for n in ("playwright", "chromium", "network", "session")),
          str(names))

    # ---------- 10. Risk levels ----------
    print("\n[10] Risk levels: overwriting the login session requires --yes")
    r = run(*g, "cookies", "import", str(ns))          # the default profile already has a healthy session at this point
    check("overwrite login session without --yes → exit code 1", r.returncode == 1, f"{r.returncode}")
    check("hint gives --yes and the alternative", "yes" in r.stderr.lower() and "--profile" in r.stderr,
          r.stderr[-200:])
    check("overwrite allowed only with --yes", run(*g, "cookies", "import", str(ns), "--yes").returncode == 0)
    d = as_json(run(*g, "--json", "--dry-run", "cookies", "import", str(ns)))
    check("dry-run is not blocked (preview only)", d.get("dry_run") is True, str(d)[:160])

    # ---------- 11. --no-wait + jobs ----------
    print("\n[11] --no-wait background jobs")
    d = as_json(run(*g, "--json", "--no-wait", "ask", "background job test"))
    job_id = d.get("data", {}).get("job_id", "")
    check("got a job_id", d.get("ok") is True and job_id, str(d)[:200])
    check("returns immediately (status=running)", d.get("data", {}).get("status") == "running", str(d)[:160])

    deadline = time.time() + 90
    status = ""
    while time.time() < deadline:
        time.sleep(2)
        sd = as_json(run("--json", "jobs", "status", job_id))
        status = sd.get("data", {}).get("status", "")
        if status in ("done", "failed", "lost"):
            break
    check("job completed", status == "done", f"status={status}")

    r = run("jobs", "result", job_id)
    check("jobs result outputs the answer", "background job test" in r.stdout, r.stdout[:160])
    check("jobs result --jq works",
          "background job test" in run("--jq", ".data.answer", "jobs", "result", job_id).stdout)
    check("jobs list contains this job", job_id in run("jobs", "list").stdout)
    check("jobs logs have content", len(run("jobs", "logs", job_id, "--tail", "10").stdout.strip()) > 0)
    d = as_json(run("--json", "jobs", "list"))
    check("jobs list --json", any(j["job_id"] == job_id for j in d.get("data", {}).get("jobs", [])),
          str(d)[:200])
    check("jobs cancel succeeded", run("jobs", "cancel", job_id).returncode == 0)

    # ---------- 12. schema ----------
    print("\n[12] arena schema")
    d = as_json(run("--json", "schema"))
    check("lists all commands", len(d.get("data", {}).get("commands", [])) >= 10, str(d)[:160])
    d = as_json(run("--json", "schema", "ask"))
    info = d.get("data", {})
    check("ask risk is write", info.get("risk") == "write", str(info)[:160])
    check("has a parameter list", any(p.get("arg") == "prompt" for p in info.get("params", [])), str(info)[:240])
    d = as_json(run("--json", "schema", "profile", "delete"))
    check("profile delete is marked high-risk-write",
          d.get("data", {}).get("risk") == "high-risk-write", str(d)[:160])
    r = run("--json", "schema", "nope")
    check("unknown command → 64", r.returncode == 64, f"{r.returncode}")
    check("gives a hint listing available commands",
          "unknown_command" in r.stdout and "hint" in r.stdout, r.stdout[:200])
    check("human-readable schema output", "risk" in run("schema", "cookies", "import").stdout)
    d = as_json(run("--json", "schema", "workspace.download"))
    check("schema supports dotted notation", str(d.get("data", {}).get("command", "")).endswith("download"), str(d)[:160])
    r = run("schema", "workspace.bogus")
    check("unknown subcommand → 64 and lists subcommands",
          r.returncode == 64 and "download" in r.stderr, r.stderr[-140:])
    check("--proxy is no longer mixed into each command's parameter table",
          "--proxy" not in run("schema", "ask").stdout, run("schema", "ask").stdout[:200])

    # ---------- 14. workspace ----------
    print("\n[14] arena workspace")
    d = as_json(run(*g, "--json", "workspace", "list"))
    paths = [f.get("path") for f in d.get("data", {}).get("files", [])]
    check("list got the files", d.get("ok") is True and len(paths) >= 3, str(d)[:200])
    check("paths include the folder prefix", "uploads/note.txt" in paths, str(paths))
    check("stats parsed the file count", (d.get("data", {}).get("stats", {}).get("files") or 0) >= 3,
          str(d.get("data", {}).get("stats")))
    r = run(*g, "workspace", "show", "uploads/note.txt")
    check("show prints the content", "hello from mock" in r.stdout, r.stdout[:120])
    check("show stdout contains only the content", not r.stdout.lstrip().startswith("{"), r.stdout[:60])
    d2 = as_json(run(*g, "--json", "workspace", "show", "hello.md"))
    check("show accepts a bare filename", str(d2.get("data", {}).get("content", "")).startswith("# Mock"), str(d2)[:160])
    r = run(*g, "workspace", "show", "image.png")
    check("binary file refuses to print", r.returncode == 1 and "download" in r.stderr, r.stderr[-160:])

    dl = HOME / "dl"
    target = dl / "note.txt"
    r = run(*g, "workspace", "download", "uploads/note.txt", "--out", str(target))
    check("download saved to disk", target.exists() and "hello" in target.read_text(), r.stderr[-160:])
    dj = as_json(run(*g, "--json", "workspace", "download", "hello.md", "--out", str(dl / "hello.md")))
    check("download returns sha256/size", dj.get("data", {}).get("sha256") and dj["data"]["size"] > 0,
          str(dj)[:200])
    r = run(*g, "workspace", "download", "hello.md", "--out", str(target))
    check("overwriting a local file requires --yes", r.returncode == 1 and "yes" in r.stderr.lower(), r.stderr[-160:])
    check("--yes allows the overwrite",
          run(*g, "workspace", "download", "hello.md", "--out", str(target), "--yes").returncode == 0)
    zip_out = dl / "ws.zip"
    r = run(*g, "workspace", "download", "--all", "--out", str(zip_out))
    import zipfile
    ok_zip = zip_out.exists() and zipfile.ZipFile(zip_out).namelist() != []
    check("--all bulk download", r.returncode == 0 and ok_zip, r.stderr[-200:])
    check("nonexistent file reports an error", run(*g, "workspace", "show", "nope.txt").returncode == 1)

    # ---------- 15. --proxy ----------
    print("\n[15] --proxy (requests really go through the proxy)")
    psrv = proxy_server.serve(0)
    pport = psrv.server_address[1]
    threading.Thread(target=psrv.serve_forever, daemon=True).start()
    proxy_url = f"http://127.0.0.1:{pport}"
    d = as_json(run(*g, "--json", "--proxy", proxy_url, "workspace", "list"), timeout=180)
    check("still works through the proxy", d.get("ok") is True, str(d)[:200])
    reqs = list(psrv.state["requests"])
    check("proxy actually received requests", len(reqs) > 0, str(psrv.state["errors"])[:200])
    check("proxy saw requests to the target site", any("127.0.0.1" in u for u in reqs), str(reqs[:3])[:200])
    st = as_json(run(*g, "--json", "--proxy", proxy_url, "status"))
    check("status shows the proxy", f"127.0.0.1:{pport}" in str(st.get("data", {}).get("proxy")), str(st)[:200])
    run(*g, "config", "set", "proxy", proxy_url)
    before = len(psrv.state["requests"])
    d = as_json(run(*g, "--json", "workspace", "list"), timeout=180)
    check("proxy in the config file also takes effect",
          d.get("ok") is True and len(psrv.state["requests"]) > before, str(d)[:160])
    run(*g, "config", "unset", "proxy")
    r = run(*g, "--json", "--proxy", "http://127.0.0.1:9", "workspace", "list", timeout=180)
    check("bad proxy fails explicitly (does not pretend success)",
          r.returncode != 0 or '"ok": false' in r.stdout, r.stdout[-200:])
    check("bad proxy's error envelope is readable",
          "proxy" in (r.stdout + r.stderr).lower() or "browser_error" in (r.stdout + r.stderr).lower(),
          (r.stdout + r.stderr)[-200:])
    psrv.shutdown()

    # ---------- 13. Error envelope ----------
    print("\n[13] Unified error envelope")
    d = as_json(run("--json", "--base-url", "http://127.0.0.1:1", "ask", "x"))
    err = d.get("error", {})
    check("ok=false with type/subtype/message/hint",
          d.get("ok") is False and all(k in err for k in ("type", "subtype", "message", "hint")),
          str(d)[:240])
    r = run("--json", "--base-url", base, "--profile", "no-such-profile", "ask", "x")
    d = as_json(r)
    check("profile with no session → auth.login_required",
          d.get("error", {}).get("subtype") in ("login_required", "browser_error"),
          str(d)[:240])

    srv.shutdown()
    shutil.rmtree(HOME, ignore_errors=True)
    print("\n" + ("All passed ✅" if not FAILED else f"{len(FAILED)} failed ❌: {FAILED}"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
