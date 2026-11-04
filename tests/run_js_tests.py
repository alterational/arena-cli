#!/usr/bin/env python3
"""
run_js_tests.py — verifies snippets/*.js (the browser_exec-only route, where only JS can be executed)

Runs once against the local mock site:
  03_login_email.js(STEP=terms) -> 01_fill_and_send.js -> 02_wait_and_read.js until done
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import mock_server  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILED.append(name)


def snippet(name: str, **repl: str) -> str:
    js = (ROOT / "snippets" / name).read_text(encoding="utf-8")
    for k, v in repl.items():
        js = js.replace(k, v)
    return js


def main() -> int:
    srv = mock_server.serve(0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/agent"
    print(f"mock server: {url}\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "mock_session", "value": "1",
                          "domain": "127.0.0.1", "path": "/"}])
        page = ctx.new_page()
        page.goto(url)
        page.wait_for_timeout(800)

        print("[1] 03_login_email.js STEP=terms (dismiss the Terms dialog)")
        res = json.loads(page.evaluate(snippet("03_login_email.js")))
        check("Terms handled", res.get("ok") is True, str(res))
        check("Dialog gone", page.locator('[role=dialog]:has-text("Terms of Use")').count() == 0)

        print("\n[2] 01_fill_and_send.js (write and send; includes multiple lines and special characters)")
        prompt = "Do three things:\n1. Open the web page\n2. Remember cookies\n3. Fetch the answer <ok> & \"quote\" café"
        js = snippet("01_fill_and_send.js").replace("__PROMPT__", prompt.replace("`", "\\`"))
        res = json.loads(page.evaluate(js))
        check("Send succeeded", res.get("ok") is True, str(res))

        print("\n[3] 02_wait_and_read.js (polling until done)")
        head = prompt.split("\n")[0][:40]
        js2 = snippet("02_wait_and_read.js").replace("__PROMPT_HEAD__", head.replace("`", "\\`"))
        answer, done = "", False
        for _ in range(40):
            page.wait_for_timeout(600)
            r = json.loads(page.evaluate(js2))
            answer = r.get("answer", "")
            if r.get("done"):
                done = True
                break
        check("Completion marker appeared", done, str(r))
        check("Answer content is correct", "Remember cookies" in answer and "café" in answer, answer[:200])
        check("Special characters were not mangled", "<ok>" in answer, answer[:200])
        check("No leftover Stop button", page.locator('button[aria-label*="Stop"]').count() == 0)

        print("\n[4] 04_dump_session.js")
        r = json.loads(page.evaluate(snippet("04_dump_session.js")))
        check("localStorage can be exported", r.get("ok") is True and "localStorage" in r, str(r)[:200])

        browser.close()

    srv.shutdown()
    print("\n" + ("All passed ✅" if not FAILED else f"{len(FAILED)} failed ❌: {FAILED}"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
