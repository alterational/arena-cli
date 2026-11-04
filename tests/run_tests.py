#!/usr/bin/env python3
"""
run_tests.py — end-to-end self-test: verifies the full arena_agent.py pipeline against the local mock site.

Covers:
  1. Sending while not logged in -> must report LOGIN_REQUIRED (exit code 2), not "pretend it succeeded"
  2. login command -> completes Terms + email login -> saves state.json
  3. A brand-new browser profile carrying only the cookies from state.json -> works directly (proves "remember cookies" works)
  4. Multi-line prompt (with non-ASCII text such as café) -> sends it in full and retrieves the answer
  5. Long text / special characters are not swallowed
  6. Server 429 (rate limiting) -> RATE_LIMITED, exit code 7, must not pretend success

Run:  python tests/run_tests.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "arena_agent.py"
sys.path.insert(0, str(ROOT / "tests"))

import mock_server  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  {detail}")
        FAILED.append(name)


def run(args: list[str], timeout: int = 180) -> tuple[int, dict | str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, str(SCRIPT), *args],
                       capture_output=True, text=True, timeout=timeout, env=env)
    out = p.stdout.strip()
    try:
        return p.returncode, json.loads(out)
    except json.JSONDecodeError:
        return p.returncode, out + "\n---stderr---\n" + p.stderr[-1200:]


def main() -> int:
    srv = mock_server.serve(0)
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"mock server: {base}/agent\n")

    tmp = Path(tempfile.mkdtemp(prefix="arena-test-"))
    common = ["--base-url", base, "--format", "json",
              "--state-file", str(tmp / "state.json"),
              "--timeout", "90", "--stable", "1.0"]

    # ---------- 1. Not logged in ----------
    print("[1] Sending while not logged in should report LOGIN_REQUIRED")
    code, res = run([*common, "--profile-dir", str(tmp / "anon"), "ask", "Hello, this is a quick test"])
    check("exit code = 2", code == 2, f"got {code}")
    check("error code = LOGIN_REQUIRED", isinstance(res, dict) and res.get("code") == "LOGIN_REQUIRED", str(res)[:200])

    # ---------- 2. Login ----------
    print("\n[2] login command: Terms -> email -> save cookies")
    headed = ["--headed"] if os.environ.get("HEADED") else []
    code, res = run([*common, *headed, "--profile-dir", str(tmp / "prof"),
                     "login", "--email", "tester@example.com", "--wait", "45"], timeout=180)
    check("exit code = 0", code == 0, str(res)[:300])
    check("logged_in succeeded", isinstance(res, dict) and res.get("ok") and res.get("logged_in") is True, str(res)[:300])
    state_file = tmp / "state.json"
    check("state.json created", state_file.exists())
    cookies = json.loads(state_file.read_text())["cookies"] if state_file.exists() else []
    check("session cookie present", any(c["name"] == "mock_session" for c in cookies), str(cookies)[:200])

    # ---------- 3. New profile + old cookies ----------
    print("\n[3] Brand-new browser profile, recovering login only from state.json")
    code, res = run([*common, "--profile-dir", str(tmp / "fresh"), "ask", "Sending should work with cookies alone"])
    check("exit code = 0", code == 0, str(res)[:300])
    check("got an answer", isinstance(res, dict) and res.get("ok") is True, str(res)[:300])
    answer = res.get("answer", "") if isinstance(res, dict) else ""
    check("answer content is correct", "Sending should work with cookies alone" in answer, answer[:200])

    # ---------- 4. Multi-line prompt ----------
    print("\n[4] Multi-line prompt (non-ASCII: café)")
    prompt = "Please summarize the three points below:\nPoint one: open the web page\nPoint two: log in and remember cookies\nPoint three: retrieve the Agent's answer (café)"
    code, res = run([*common, "--profile-dir", str(tmp / "fresh"), "ask", prompt])
    ans = res.get("answer", "") if isinstance(res, dict) else str(res)
    check("exit code = 0", code == 0, str(res)[:300])
    check("multi-line content delivered in full (all three points)", all(k in ans for k in ("Point one", "Point two", "Point three")), ans[:300])

    # ---------- 5. Long text / special characters ----------
    print("\n[5] Long text + special characters")
    long_prompt = ("Testing special characters: <div> & \"quotes\" 'single' `code` \n" +
                   "lorem ipsum " * 60).strip()
    code, res = run([*common, "--profile-dir", str(tmp / "fresh"), "ask", long_prompt])
    ans = res.get("answer", "") if isinstance(res, dict) else str(res)
    check("exit code = 0", code == 0, str(res)[:300])
    check("<div> did not break the page", "<div>" in ans, ans[:200])
    check("long text not truncated (lorem ipsum appears >= 10 times)", ans.count("lorem ipsum") >= 10, str(ans.count("lorem ipsum")))

    # ---------- 6. check ----------
    print("\n[6] check command")
    code, res = run([*common, "--profile-dir", str(tmp / "fresh"), "check"])
    check("check reports logged in", isinstance(res, dict) and res.get("ok") and res.get("logged_in") is True, str(res)[:200])

    # ---------- 7. CLI input methods ----------
    print("\n[7] stdin / --file / --out: three input and output methods")
    pth = tmp / "prompt.md"
    pth.write_text("Question from file\nSecond line", encoding="utf-8")
    out_md = tmp / "out.md"
    code, res = run([*common, "--profile-dir", str(tmp / "fresh"), "ask", "--file", str(pth),
                     "--format", "markdown", "--out", str(out_md)])
    check("--file + --out succeeded", code == 0 and out_md.exists(), str(res)[:200])
    check("markdown output can be forwarded directly", "Question from file" in out_md.read_text(encoding="utf-8"), "")

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    pr = subprocess.run([sys.executable, str(SCRIPT), *common, "--profile-dir", str(tmp / "fresh"),
                         "ask", "-"], input="Question from stdin", capture_output=True, text=True, env=env)
    ok_stdin = False
    try:
        ok_stdin = "Question from stdin" in json.loads(pr.stdout)["answer"]
    except Exception:
        pass
    check("stdin (ask -) succeeded", ok_stdin, pr.stdout[:200] + pr.stderr[-300:])

    # ---------- 8. cookies import/export ----------
    print("\n[8] export-cookies / import-cookies")
    ck = tmp / "cookies.json"
    code, res = run([*common, "--profile-dir", str(tmp / "fresh"), "export-cookies",
                     "--cookies-out", str(ck)])
    check("cookies exported", code == 0 and ck.exists(), str(res)[:200])
    # Simulates the flat-array EditThisCookie format exported from a daily-use browser
    flat = [{"name": "mock_session", "value": "1", "domain": "127.0.0.1", "path": "/",
             "expirationDate": 4102444800}]
    flat_path = tmp / "flat.json"
    flat_path.write_text(json.dumps(flat), encoding="utf-8")
    code, res = run(["--state-file", str(tmp / "imported.json"), "import-cookies", str(flat_path)])
    check("flat cookies array imported", code == 0 and (tmp / "imported.json").exists(), str(res)[:200])
    code, res = run(["--base-url", base, "--format", "json", "--timeout", "90", "--stable", "1.0",
                     "--state-file", str(tmp / "imported.json"), "--profile-dir", str(tmp / "imported_prof"),
                     "ask", "Ask with the imported cookies"])
    ans = res.get("answer", "") if isinstance(res, dict) else str(res)
    check("imported cookies work directly", code == 0 and "Ask with the imported cookies" in ans, str(res)[:250])

    # ---------- 9. User-uploaded cookies file (Netscape cookies.txt) ----------
    print("\n[9] Upload Netscape cookies.txt directly -> ask with a single command")
    ns = tmp / "uploaded_cookies.txt"
    ns.write_text(
        "# Netscape HTTP Cookie File\n"
        "# https://curl.haxx.se/rfc/cookie_spec.html\n\n"
        f"127.0.0.1\tFALSE\t/\tFALSE\t4102444800\tmock_session\t1\n"
        f"127.0.0.1\tFALSE\t/\tFALSE\t4102444800\t_ga\tGA1.1.123.456\n",
        encoding="utf-8")
    code, res = run(["--base-url", base, "--format", "json", "--cookies", str(ns),
                     "--profile-dir", str(tmp / "from_txt"), "--state-file", str(tmp / "txt_state.json"),
                     "ask", "Ask with the uploaded cookies"], timeout=180)
    ans = res.get("answer", "") if isinstance(res, dict) else str(res)
    check("Netscape cookies accepted directly", code == 0 and "Ask with the uploaded cookies" in ans, str(res)[:300])

    code, res = run(["--state-file", str(tmp / "imp2.json"), "import-cookies", str(ns)])
    check("import recognized as netscape format", isinstance(res, dict) and res.get("format") == "netscape", str(res)[:200])
    check("session cookie recognized", isinstance(res, dict) and res.get("has_auth_cookie") is True, str(res)[:200])

    # ---------- 10. Structural parsing of a real uploaded file (offline, no network) ----------
    real = Path("/home/user/uploads/arena.ai_cookies.txt")
    print("\n[10] Parse the actual arena.ai_cookies.txt uploaded by the user")
    if real.exists():
        code, res = run(["--state-file", str(tmp / "real.json"), "import-cookies", str(real)])
        check("parsing succeeded", code == 0 and isinstance(res, dict) and res.get("ok"), str(res)[:300])
        check("arena-auth session cookie captured",
              isinstance(res, dict) and any("arena-auth" in n for n in res.get("auth_cookie_names", [])),
              str(res)[:300])
        check("10+ cookies recognized", isinstance(res, dict) and res.get("count", 0) >= 10, str(res)[:200])
        check("state file permissions 600",
              oct((tmp / "real.json").stat().st_mode)[-3:] == "600",
              oct((tmp / "real.json").stat().st_mode)[-3:])
        # Cookie values must not be written to the output (prevents leaks)
        blob = json.dumps(res, ensure_ascii=False)
        saved = json.loads((tmp / "real.json").read_text())
        first_val = saved["cookies"][0]["value"][:12] if saved.get("cookies") else "x"
        check("output does not contain cookie values", first_val not in blob, "plaintext value found!")
    else:
        print("  ⏭  Skipped (uploaded cookies file not found)")

    # ---------- 11. Bad file ----------
    print("\n[11] A file that does not look like cookies should report BAD_INPUT")
    bad = tmp / "bad.txt"
    bad.write_text("hello world, this is not a cookie file", encoding="utf-8")
    code, res = run(["--state-file", str(tmp / "bad.json"), "import-cookies", str(bad)])
    check("exit code != 0", code != 0, f"got {code}")
    check("reports BAD_INPUT and gives a format hint",
          isinstance(res, dict) and res.get("code") == "BAD_INPUT" and "Netscape" in (res.get("error") or ""),
          str(res)[:200])

    # ---------- 12. Server rate limit (429) ----------
    print("\n[12] create-chat returns 429 -> RATE_LIMITED (must not pretend success)")
    mock_server.Handler.state["rate_limit"] = True
    try:
        code, res = run([*common, "ask", "This message will be rejected by the server"], timeout=180)
    finally:
        mock_server.Handler.state["rate_limit"] = False
    check("exit code = 7", code == 7, f"got {code}")
    check("reports RATE_LIMITED",
          isinstance(res, dict) and res.get("code") == "RATE_LIMITED", str(res)[:200])
    check("hint gives a next step",
          isinstance(res, dict) and ("429" in (res.get("error") or "") or "too many requests" in (res.get("error") or "")
                                     or "cookie" in (res.get("error") or "")),
          str(res)[:200])
    # After the rate limit is lifted, asking again works (proves only that request was rejected, not that state was corrupted)
    code, res = run([*common, "ask", "Normal question after the rate limit is lifted"], timeout=180)
    check("back to normal after the limit is lifted", code == 0 and isinstance(res, dict) and res.get("ok") is True, str(res)[:200])

    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("All passed ✅" if not FAILED else f"{len(FAILED)} failed ❌: {FAILED}"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
