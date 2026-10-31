#!/usr/bin/env python3
"""
run_js_tests.py — 验证 snippets/*.js（只有 browser_exec、只能执行 JS 的路线）

用本地 mock 站点跑一遍：
  03_login_email.js(STEP=terms) -> 01_fill_and_send.js -> 02_wait_and_read.js 直到 done
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

        print("[1] 03_login_email.js STEP=terms（关掉 Terms 弹窗）")
        res = json.loads(page.evaluate(snippet("03_login_email.js")))
        check("Terms 已处理", res.get("ok") is True, str(res))
        check("弹窗已消失", page.locator('[role=dialog]:has-text("Terms of Use")').count() == 0)

        print("\n[2] 01_fill_and_send.js（写入并发送，含多行与特殊字符）")
        prompt = "帮我做三件事：\n1. 打开网页\n2. 记住 cookies\n3. 取回回答 <ok> & \"quote\""
        js = snippet("01_fill_and_send.js").replace("__PROMPT__", prompt.replace("`", "\\`"))
        res = json.loads(page.evaluate(js))
        check("发送成功", res.get("ok") is True, str(res))

        print("\n[3] 02_wait_and_read.js（轮询到 done）")
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
        check("完成标记出现", done, str(r))
        check("回答内容正确", "记住 cookies" in answer, answer[:200])
        check("特殊字符未被破坏", "<ok>" in answer, answer[:200])
        check("没有残留 Stop 按钮", page.locator('button[aria-label*="Stop"]').count() == 0)

        print("\n[4] 04_dump_session.js")
        r = json.loads(page.evaluate(snippet("04_dump_session.js")))
        check("能导出 localStorage", r.get("ok") is True and "localStorage" in r, str(r)[:200])

        browser.close()

    srv.shutdown()
    print("\n" + ("全部通过 ✅" if not FAILED else f"失败 {len(FAILED)} 项 ❌: {FAILED}"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
