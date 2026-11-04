#!/usr/bin/env python3
"""
run_entry_tests.py —— verifies the "Hermes skill entry" layer

Covers:
  1. On success, arena_ask.py's stdout contains **only the answer text** (no JSON, engine logs, or UI noise)
  2. On failure, prints one line `❌ CODE: suggestion`; the exit code can be recognized by Hermes (2 = login required)
  3. --cookies in one step (import + ask)
  4. --json output has all structured fields
  5. --max-chars truncates and appends the session link
  6. install.sh installs the skill into HERMES_HOME, and verify_skill.py still passes after installation
  7. SKILL.md frontmatter/sections comply (scripts/verify_skill.py)
  8. Data directory migrates from ~/.arena-agent to ~/.arena-cli (done only once; login state is not lost)
  9. Relative links in README / docs all point to files that exist (docs must not drift)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASK = ROOT / "scripts" / "arena_ask.py"
sys.path.insert(0, str(ROOT / "tests"))
import mock_server  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILED.append(name)


def run(args: list[str], stdin: str | None = None, env: dict | None = None,
        timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ASK), *args], capture_output=True, text=True,
                          input=stdin, timeout=timeout,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})})


def main() -> int:
    srv = mock_server.serve(0)
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"mock server: {base}/agent\n")

    tmp = Path(tempfile.mkdtemp(prefix="arena-entry-"))
    common = ["--base-url", base, "--state-file", str(tmp / "state.json")]

    # ---------- 1. Not logged in: failure must also be a "one-liner" ----------
    print("[1] No session: one error line + exit code 2")
    r = run([*common, "--profile-dir", str(tmp / "anon"), "Hello"])
    check("exit code = 2", r.returncode == 2, f"got {r.returncode}: {r.stdout!r}")
    check("stdout is a single ❌ line", r.stdout.strip().count("\n") == 0, r.stdout)
    check("message includes LOGIN_REQUIRED", "LOGIN_REQUIRED" in r.stdout, r.stdout)
    check("message tells the user the next step (cookies)", "cookies" in r.stdout.lower(), r.stdout)

    # ---------- 2. Upload cookies: one step ----------
    print("\n[2] --cookies in one step (import + ask)")
    ns = tmp / "cookies.txt"
    ns.write_text("# Netscape HTTP Cookie File\n\n"
                  "127.0.0.1\tFALSE\t/\tFALSE\t4102444800\tmock_session\t1\n", encoding="utf-8")
    r = run([*common, "--profile-dir", str(tmp / "p1"), "--cookies", str(ns),
             "Summarize my three to-dos for today"])
    answer = r.stdout.strip()
    check("exit code = 0", r.returncode == 0, f"{r.returncode}: {r.stdout!r} / {r.stderr[-300:]}")
    check("stdout is the answer text", answer.startswith("ECHO[") and "Summarize my three to-dos for today" in answer, answer[:200])
    check("no JSON braces", not answer.startswith("{"), answer[:80])
    check("no engine logs mixed into stdout", "[arena]" not in answer, answer[:200])
    check("no page UI noise", "Was this task successful?" not in answer and "Copy" not in answer, answer[:200])

    # ---------- 3. --json ----------
    print("\n[3] --json structured output")
    r = run([*common, "--profile-dir", str(tmp / "p1"), "--json", "Structured test"])
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        data = {}
    check("is valid JSON", isinstance(data, dict) and data.get("ok") is True, r.stdout[:200])
    check("all fields present", all(k in data for k in ("answer", "url", "chat_id", "elapsed_seconds")), str(data)[:200])

    # ---------- 4. --max-chars ----------
    print("\n[4] --max-chars truncates long answers")
    r = run([*common, "--profile-dir", str(tmp / "p1"), "--max-chars", "40",
             "lorem ipsum " * 30])
    out = r.stdout.strip()
    check("exit code = 0", r.returncode == 0, r.stdout[:200])
    check("truncated and marked", "truncated" in out, out[-120:])
    check("session link appended", "http" in out, out[-160:])

    # ---------- 5. stdin ----------
    print("\n[5] stdin input (when no arguments are given after /arena-cli)")
    r = run([*common, "--profile-dir", str(tmp / "p1"), "-"], stdin="Question from stdin")
    check("stdin works", r.returncode == 0 and "Question from stdin" in r.stdout, r.stdout[:200])

    # ---------- 6. Install into HERMES_HOME ----------
    print("\n[6] install.sh into HERMES_HOME")
    fake_home = tmp / "hermes_home"
    env = {**os.environ, "HERMES_HOME": str(fake_home)}
    r = subprocess.run(["bash", str(ROOT / "install.sh")], capture_output=True, text=True, env=env)
    dest = fake_home / "skills" / "arena-cli"
    check("exit code = 0", r.returncode == 0, r.stdout[-300:] + r.stderr[-300:])
    check("SKILL.md in place", (dest / "SKILL.md").exists())
    check("entry script in place", (dest / "scripts" / "arena_ask.py").exists())
    check("maintenance manual in place", (dest / "references" / "troubleshooting.md").exists())
    check("state.json not packaged", not list(dest.rglob("state.json")))
    # Run the self-check again after installation
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_skill.py"), str(ROOT)],
                       capture_output=True, text=True)
    check("verify_skill passes", r.returncode == 0, r.stdout[-300:])
    # Repeated installation should be rejected
    r2 = subprocess.run(["bash", str(ROOT / "install.sh")], capture_output=True, text=True, env=env)
    check("repeat install rejected (needs --force)", r2.returncode != 0, r2.stdout[-200:])

    # ---------- 7. Slash command name = name field ----------
    print("\n[7] Slash command name matches the frontmatter")
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    import yaml  # noqa: WPS433
    fm = yaml.safe_load(text.split("---", 2)[1])
    check("name = arena-cli (i.e. /arena-cli)", fm.get("name") == "arena-cli", str(fm.get("name")))
    check("declares requires_toolsets: terminal",
          "terminal" in (fm.get("metadata", {}).get("hermes", {}).get("requires_toolsets") or []))
    check("description is short (<= 60)", len(fm.get("description", "")) <= 60,
          f"{len(fm.get('description', ''))} characters")

    # ---------- 8. Data directory migration ----------
    print("\n[8] ~/.arena-agent → ~/.arena-cli migration")
    mig = Path(tmp) / "mig"

    def migrate(home: Path, code: str = "from arena.config import arena_home; print(arena_home())") -> str:
        env2 = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
        return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              env=env2, cwd=str(ROOT)).stdout.strip()

    # 8a. New directory does not exist → rename the whole directory; state.json moves unchanged
    (mig / ".arena-agent").mkdir(parents=True)
    (mig / ".arena-agent" / "state.json").write_text('{"cookies": 1}', encoding="utf-8")
    out = migrate(mig)
    check("renamed: returns ~/.arena-cli", out.endswith(".arena-cli"), out)
    check("state.json moved along", (mig / ".arena-cli" / "state.json").read_text() == '{"cookies": 1}')
    check("old directory gone", not (mig / ".arena-agent").exists())
    check("migration marker written", (mig / ".arena-cli" / ".migrated-from-arena-agent").exists())

    # 8b. New directory already exists (another command ran first) → merge item by item; missing items move over, same-name files keep the newer one
    mig2 = Path(tmp) / "mig2"
    (mig2 / ".arena-cli" / "jobs").mkdir(parents=True)
    (mig2 / ".arena-cli" / "jobs" / "j.json").write_text("{}", encoding="utf-8")
    (mig2 / ".arena-cli" / "config.json").write_text('{"new": true}', encoding="utf-8")
    os.utime(mig2 / ".arena-cli" / "config.json", (1, 1))          # the one in the target is older
    (mig2 / ".arena-agent" / "prof" / "Default").mkdir(parents=True)
    (mig2 / ".arena-agent" / "state.json").write_text('{"cookies": 2}', encoding="utf-8")
    (mig2 / ".arena-agent" / "config.json").write_text('{"old": true}', encoding="utf-8")
    (mig2 / ".arena-agent" / "prof" / "Default" / "Cookies").write_text("c", encoding="utf-8")
    migrate(mig2)
    check("state.json in place after merge", (mig2 / ".arena-cli" / "state.json").exists())
    check("existing jobs/ not overwritten", (mig2 / ".arena-cli" / "jobs" / "j.json").exists())
    check("missing profile files moved over", (mig2 / ".arena-cli" / "prof" / "Default" / "Cookies").exists())
    check("same-name file keeps the newer one", (mig2 / ".arena-cli" / "config.json").read_text() == '{"old": true}',
          (mig2 / ".arena-cli" / "config.json").read_text())

    # 8c. Migration runs only once (not rescanned the second time)
    check("old directory emptied and removed", not (mig2 / ".arena-agent").exists())
    (mig2 / ".arena-agent").mkdir()
    (mig2 / ".arena-agent" / "late.json").write_text("x", encoding="utf-8")
    migrate(mig2)
    check("runs only once: files placed in the old directory later are not moved", not (mig2 / ".arena-cli" / "late.json").exists())

    # 8d. No migration when ARENA_HOME is set explicitly
    mig3 = Path(tmp) / "mig3"
    (mig3 / ".arena-agent").mkdir(parents=True)
    env3 = {"HOME": str(mig3), "ARENA_HOME": str(mig3 / "mine"), "PATH": os.environ.get("PATH", "")}
    out = subprocess.run([sys.executable, "-c",
                          "from arena.config import arena_home; print(arena_home())"],
                         capture_output=True, text=True, env=env3, cwd=str(ROOT)).stdout.strip()
    check("ARENA_HOME takes precedence", out == str(mig3 / "mine"), out)
    check("old directory untouched under ARENA_HOME", (mig3 / ".arena-agent").exists())

    # ---------- 9. Doc links ----------
    print("\n[9] Relative links in README / docs")
    import re

    docs = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")),
            ROOT / "references" / "troubleshooting.md", ROOT / "SKILL.md"]
    broken: list[str] = []
    for md in docs:
        for target in re.findall(r"\]\(([^)\s]+)\)", md.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = (md.parent / target.split("#")[0]).resolve()
            if not path.exists():
                broken.append(f"{md.relative_to(ROOT)} → {target}")
    check("all links valid", not broken, "; ".join(broken))
    check("README is short (<= 120 lines; details go in docs/)",
          len((ROOT / "README.md").read_text(encoding="utf-8").splitlines()) <= 120,
          f"{(ROOT / 'README.md').read_text(encoding='utf-8').count(chr(10))} lines")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for topic in ("docs/cli.md", "docs/agent.md", "docs/auth.md",
                  "docs/hermes-skill.md", "docs/development.md", "docs/migration.md"):
        check(f"README index includes {topic}", topic in readme)

    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("All passed ✅" if not FAILED else f"{len(FAILED)} failed ❌: {FAILED}"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
