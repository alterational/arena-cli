#!/usr/bin/env python3
"""
run_tests.py — 端到端自测：用本地 mock 站点验证 arena_agent.py 的完整链路。

覆盖：
  1. 未登录发送 -> 必须报 LOGIN_REQUIRED（退出码 2），而不是"假装成功"
  2. login 命令 -> 走完 Terms + 邮箱登录 -> 保存 state.json
  3. 换一个全新的浏览器 profile、只带 state.json 里的 cookies -> 直接可用（证明"记住 cookies"生效）
  4. 多行中文 prompt -> 完整发送并取回回答
  5. 长文本 / 特殊字符不会被吃掉
  6. 服务端 429（限流）-> RATE_LIMITED，退出码 7，不许假装成功

运行：  python tests/run_tests.py
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

    # ---------- 1. 未登录 ----------
    print("[1] 未登录时发送应报 LOGIN_REQUIRED")
    code, res = run([*common, "--profile-dir", str(tmp / "anon"), "ask", "你好，测试一下"])
    check("退出码 = 2", code == 2, f"got {code}")
    check("错误码 = LOGIN_REQUIRED", isinstance(res, dict) and res.get("code") == "LOGIN_REQUIRED", str(res)[:200])

    # ---------- 2. 登录 ----------
    print("\n[2] login 命令：Terms -> 邮箱 -> 保存 cookies")
    headed = ["--headed"] if os.environ.get("HEADED") else []
    code, res = run([*common, *headed, "--profile-dir", str(tmp / "prof"),
                     "login", "--email", "tester@example.com", "--wait", "45"], timeout=180)
    check("退出码 = 0", code == 0, str(res)[:300])
    check("logged_in 成功", isinstance(res, dict) and res.get("ok") and res.get("logged_in") is True, str(res)[:300])
    state_file = tmp / "state.json"
    check("state.json 已生成", state_file.exists())
    cookies = json.loads(state_file.read_text())["cookies"] if state_file.exists() else []
    check("含会话 cookie", any(c["name"] == "mock_session" for c in cookies), str(cookies)[:200])

    # ---------- 3. 新 profile + 旧 cookies ----------
    print("\n[3] 全新浏览器 profile，仅靠 state.json 恢复登录")
    code, res = run([*common, "--profile-dir", str(tmp / "fresh"), "ask", "只用 cookies 也要能发出去"])
    check("退出码 = 0", code == 0, str(res)[:300])
    check("拿到回答", isinstance(res, dict) and res.get("ok") is True, str(res)[:300])
    answer = res.get("answer", "") if isinstance(res, dict) else ""
    check("回答内容正确", "只用 cookies 也要能发出去" in answer, answer[:200])

    # ---------- 4. 多行中文 ----------
    print("\n[4] 多行中文 prompt")
    prompt = "请帮我总结下面三点：\n第一点：打开网页\n第二点：登录并记住 cookies\n第三点：取回 Agent 的回答"
    code, res = run([*common, "--profile-dir", str(tmp / "fresh"), "ask", prompt])
    ans = res.get("answer", "") if isinstance(res, dict) else str(res)
    check("退出码 = 0", code == 0, str(res)[:300])
    check("多行内容完整送达（含三点）", all(k in ans for k in ("第一点", "第二点", "第三点")), ans[:300])

    # ---------- 5. 长文本 / 特殊字符 ----------
    print("\n[5] 长文本 + 特殊字符")
    long_prompt = ("测试特殊字符： <div> & \"quotes\" 'single' `code` \n" +
                   "lorem ipsum " * 60).strip()
    code, res = run([*common, "--profile-dir", str(tmp / "fresh"), "ask", long_prompt])
    ans = res.get("answer", "") if isinstance(res, dict) else str(res)
    check("退出码 = 0", code == 0, str(res)[:300])
    check("<div> 未破坏页面", "<div>" in ans, ans[:200])
    check("长文本未截断（lorem 出现 >= 10 次）", ans.count("lorem ipsum") >= 10, str(ans.count("lorem ipsum")))

    # ---------- 6. check ----------
    print("\n[6] check 命令")
    code, res = run([*common, "--profile-dir", str(tmp / "fresh"), "check"])
    check("check 报告已登录", isinstance(res, dict) and res.get("ok") and res.get("logged_in") is True, str(res)[:200])

    # ---------- 7. CLI 输入方式 ----------
    print("\n[7] stdin / --file / --out 三种输入输出方式")
    pth = tmp / "prompt.md"
    pth.write_text("文件里的问题\n第二行", encoding="utf-8")
    out_md = tmp / "out.md"
    code, res = run([*common, "--profile-dir", str(tmp / "fresh"), "ask", "--file", str(pth),
                     "--format", "markdown", "--out", str(out_md)])
    check("--file + --out 成功", code == 0 and out_md.exists(), str(res)[:200])
    check("markdown 输出可直接转发", "文件里的问题" in out_md.read_text(encoding="utf-8"), "")

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    pr = subprocess.run([sys.executable, str(SCRIPT), *common, "--profile-dir", str(tmp / "fresh"),
                         "ask", "-"], input="来自 stdin 的问题", capture_output=True, text=True, env=env)
    ok_stdin = False
    try:
        ok_stdin = "来自 stdin 的问题" in json.loads(pr.stdout)["answer"]
    except Exception:
        pass
    check("stdin（ask -）成功", ok_stdin, pr.stdout[:200] + pr.stderr[-300:])

    # ---------- 8. cookies 导入导出 ----------
    print("\n[8] export-cookies / import-cookies")
    ck = tmp / "cookies.json"
    code, res = run([*common, "--profile-dir", str(tmp / "fresh"), "export-cookies",
                     "--cookies-out", str(ck)])
    check("导出 cookies", code == 0 and ck.exists(), str(res)[:200])
    # 模拟"从日常浏览器导出"的 EditThisCookie 扁平数组格式
    flat = [{"name": "mock_session", "value": "1", "domain": "127.0.0.1", "path": "/",
             "expirationDate": 4102444800}]
    flat_path = tmp / "flat.json"
    flat_path.write_text(json.dumps(flat), encoding="utf-8")
    code, res = run(["--state-file", str(tmp / "imported.json"), "import-cookies", str(flat_path)])
    check("导入扁平 cookies 数组", code == 0 and (tmp / "imported.json").exists(), str(res)[:200])
    code, res = run(["--base-url", base, "--format", "json", "--timeout", "90", "--stable", "1.0",
                     "--state-file", str(tmp / "imported.json"), "--profile-dir", str(tmp / "imported_prof"),
                     "ask", "用导入的 cookies 提问"])
    ans = res.get("answer", "") if isinstance(res, dict) else str(res)
    check("导入的 cookies 可直接用", code == 0 and "用导入的 cookies 提问" in ans, str(res)[:250])

    # ---------- 9. 用户上传的 cookies 文件（Netscape cookies.txt） ----------
    print("\n[9] 直接上传 Netscape cookies.txt -> 一条命令就能提问")
    ns = tmp / "uploaded_cookies.txt"
    ns.write_text(
        "# Netscape HTTP Cookie File\n"
        "# https://curl.haxx.se/rfc/cookie_spec.html\n\n"
        f"127.0.0.1\tFALSE\t/\tFALSE\t4102444800\tmock_session\t1\n"
        f"127.0.0.1\tFALSE\t/\tFALSE\t4102444800\t_ga\tGA1.1.123.456\n",
        encoding="utf-8")
    code, res = run(["--base-url", base, "--format", "json", "--cookies", str(ns),
                     "--profile-dir", str(tmp / "from_txt"), "--state-file", str(tmp / "txt_state.json"),
                     "ask", "用上传的 cookies 提问"], timeout=180)
    ans = res.get("answer", "") if isinstance(res, dict) else str(res)
    check("Netscape cookies 直接被采用", code == 0 and "用上传的 cookies 提问" in ans, str(res)[:300])

    code, res = run(["--state-file", str(tmp / "imp2.json"), "import-cookies", str(ns)])
    check("import 识别为 netscape 格式", isinstance(res, dict) and res.get("format") == "netscape", str(res)[:200])
    check("识别出会话 cookie", isinstance(res, dict) and res.get("has_auth_cookie") is True, str(res)[:200])

    # ---------- 10. 真实上传文件的结构解析（离线，不联网） ----------
    real = Path("/home/user/uploads/arena.ai_cookies.txt")
    print("\n[10] 解析用户实际上传的 arena.ai_cookies.txt")
    if real.exists():
        code, res = run(["--state-file", str(tmp / "real.json"), "import-cookies", str(real)])
        check("解析成功", code == 0 and isinstance(res, dict) and res.get("ok"), str(res)[:300])
        check("抓到 arena-auth 会话 cookie",
              isinstance(res, dict) and any("arena-auth" in n for n in res.get("auth_cookie_names", [])),
              str(res)[:300])
        check("识别出 10+ 条 cookie", isinstance(res, dict) and res.get("count", 0) >= 10, str(res)[:200])
        check("state 文件权限 600",
              oct((tmp / "real.json").stat().st_mode)[-3:] == "600",
              oct((tmp / "real.json").stat().st_mode)[-3:])
        # 不该把 cookie 值写进输出（防泄露）
        blob = json.dumps(res, ensure_ascii=False)
        saved = json.loads((tmp / "real.json").read_text())
        first_val = saved["cookies"][0]["value"][:12] if saved.get("cookies") else "x"
        check("输出里不含 cookie 值", first_val not in blob, "发现明文值！")
    else:
        print("  ⏭  跳过（没找到上传的 cookies 文件）")

    # ---------- 11. 坏文件 ----------
    print("\n[11] 不像 cookies 的文件应报 BAD_INPUT")
    bad = tmp / "bad.txt"
    bad.write_text("hello world, this is not a cookie file", encoding="utf-8")
    code, res = run(["--state-file", str(tmp / "bad.json"), "import-cookies", str(bad)])
    check("退出码 != 0", code != 0, f"got {code}")
    check("报 BAD_INPUT 且给出格式提示",
          isinstance(res, dict) and res.get("code") == "BAD_INPUT" and "Netscape" in (res.get("error") or ""),
          str(res)[:200])

    # ---------- 12. 服务端限流（429） ----------
    print("\n[12] create-chat 返回 429 -> RATE_LIMITED（不许假装成功）")
    mock_server.Handler.state["rate_limit"] = True
    try:
        code, res = run([*common, "ask", "这条消息会被服务端拒绝"], timeout=180)
    finally:
        mock_server.Handler.state["rate_limit"] = False
    check("退出码 = 7", code == 7, f"got {code}")
    check("报 RATE_LIMITED",
          isinstance(res, dict) and res.get("code") == "RATE_LIMITED", str(res)[:200])
    check("提示里给了下一步动作",
          isinstance(res, dict) and ("429" in (res.get("error") or "") or "限流" in (res.get("error") or "")
                                     or "cookie" in (res.get("error") or "")),
          str(res)[:200])
    # 限流解除后又能正常提问（证明只是那次被拒，不是把状态搞坏了）
    code, res = run([*common, "ask", "限流解除后的正常提问"], timeout=180)
    check("解除后恢复正常", code == 0 and isinstance(res, dict) and res.get("ok") is True, str(res)[:200])

    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("全部通过 ✅" if not FAILED else f"失败 {len(FAILED)} 项 ❌: {FAILED}"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
