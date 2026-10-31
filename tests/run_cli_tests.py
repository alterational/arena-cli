#!/usr/bin/env python3
"""
run_cli_tests.py —— 验证 `arena` CLI（对齐 lark-cli 的那套体系）

覆盖：
  1. 顶层：--help / version / python -m arena / bin/arena
  2. 用法错误 → 64
  3. config：set / get / list / show / unset / path（类型自动还原）+ 全局 flag 前后位置
  4. cookies：import（首次）/ show / dry-run
  5. check / status
  6. ask：正文 / 信封 / jq / max-chars / stdin / --file
  7. chat：list / last / open
  8. profile：create / list / use / delete（高危需 --yes）
  9. doctor
 10. 信封结构 {ok, identity, data, meta} 与错误 {ok:false, error:{type,subtype,message,hint}}
 11. --dry-run：ask / cookies import / profile delete 都不该真的执行
 12. 风险分级：覆盖登录态的 cookies import 需要 --yes
 13. --no-wait + jobs：list / status / result / logs / cancel
 14. schema：列表 / 单命令 / 未知命令
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


def as_json(proc) -> dict:
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

    # ---------- 1. 顶层 ----------
    print("[1] 顶层命令")
    r = run("--help")
    check("--help 退出码 0", r.returncode == 0)
    check("帮助里有 AGENT QUICKSTART", "AGENT QUICKSTART" in r.stdout, r.stdout[:200])
    check("帮助里列出全部命令",
          all(c in r.stdout for c in ("ask", "login", "check", "status", "cookies", "chat",
                                      "config", "profile", "jobs", "schema", "doctor")), "")
    check("version 正常", run("version").stdout.strip().startswith("arena 2."),
          run("version").stdout[:80])
    r = subprocess.run([sys.executable, "-m", "arena", "version"], cwd=ROOT,
                       capture_output=True, text=True, env={**os.environ, "ARENA_HOME": str(HOME)})
    check("python3 -m arena 可用", r.returncode == 0 and r.stdout.startswith("arena 2."), r.stdout[:80])

    # ---------- 2. 用法错误 ----------
    print("\n[2] 用法错误 → 64")
    check("缺参数 = 64", run("config", "get").returncode == 64)
    check("未知 flag = 64", run("ask", "--nope").returncode == 64)
    check("未知子命令 = 64", run("cookies", "bogus").returncode == 64)

    # ---------- 3. config ----------
    print("\n[3] arena config")
    check("set 成功", run("config", "set", "timeout", "1800").returncode == 0)
    check("get 返回 1800", run("config", "get", "timeout").stdout.strip() == "1800",
          run("config", "get", "timeout").stdout)
    run("config", "set", "base_url", base)
    check("get 返回字符串", base in run("config", "get", "base_url").stdout)
    run("config", "set", "color", "false")
    d = as_json(run("--json", "config", "get", "color"))
    check("类型自动还原 false → bool", d.get("data", {}).get("color") is False, str(d)[:200])
    r = run("config", "list")
    check("list 显示生效值", "1800" in r.stdout and "profile" in r.stdout, r.stdout[:200])
    check("show 是 list 的别名", run("config", "show").returncode == 0)
    check("path 指向 ARENA_HOME", str(HOME / "config.json") in run("config", "path").stdout)
    check("未知配置项被拒", run("config", "set", "nope", "1").returncode == 1)
    check("unset 成功", run("config", "unset", "color").returncode == 0)

    # ---------- 4. cookies ----------
    print("\n[4] arena cookies")
    ns = HOME / "up.txt"
    ns.write_text("# Netscape HTTP Cookie File\n\n"
                  "127.0.0.1\tFALSE\t/\tFALSE\t4102444800\tmock_session\t1\n"
                  "127.0.0.1\tFALSE\t/\tFALSE\t4102444800\tarena-auth-prod-v1.1\tabcdef\n",
                  encoding="utf-8")

    print("  · dry-run 不应落盘")
    r = run(*g, "--dry-run", "cookies", "import", str(ns))
    d = as_json(run(*g, "--json", "--dry-run", "cookies", "import", str(ns)))
    check("dry-run 标记存在", d.get("dry_run") is True, str(d)[:200])
    check("dry-run 报告会覆盖=否", d.get("data", {}).get("would_overwrite") is False, str(d)[:200])
    check("dry-run 没创建 state 文件", not (HOME / "state.json").exists())

    r = run(*g, "cookies", "import", str(ns))
    check("首次 import 成功（无需 --yes）", r.returncode == 0, r.stderr[-200:])
    check("报告了登录凭证", "arena-auth-prod-v1.1" in r.stdout, r.stdout[:200])
    d = as_json(run(*g, "--json", "cookies", "show"))
    check("show --json 只给名字不给值", "abcdef" not in json.dumps(d), str(d)[:200])

    # ---------- 5. check / status ----------
    print("\n[5] check / status")
    r = run(*g, "check")
    check("已登录 → 退出码 0", r.returncode == 0, f"{r.returncode}: {r.stdout[:200]}")
    d = as_json(run(*g, "--json", "check"))
    check("信封里有 logged_in", d.get("data", {}).get("logged_in") is True, str(d)[:200])
    d = as_json(run(*g, "--json", "status"))
    check("status 有 session 概览", d.get("data", {}).get("session", {}).get("has_auth_cookie") is True,
          str(d)[:200])

    # ---------- 6. ask ----------
    print("\n[6] arena ask")
    r = run(*g, "ask", "帮我总结三个待办")
    check("正文输出", r.returncode == 0 and r.stdout.strip().startswith("ECHO["), r.stdout[:160])
    check("stdout 干净", "[arena]" not in r.stdout and not r.stdout.strip().startswith("{"), "")
    d = as_json(run(*g, "--json", "ask", "结构化一下"))
    check("信封结构 ok/identity/data/meta",
          d.get("ok") is True and d.get("identity") == "default"
          and isinstance(d.get("data"), dict) and "version" in d.get("meta", {}), str(d)[:240])
    check("data 里有 answer/url",
          "answer" in d.get("data", {}) and "url" in d.get("data", {}), str(d.get("data"))[:200])
    check("全局 flag 写在命令后面也生效", as_json(run(*g, "ask", "后面", "--json")).get("ok") is True)
    out = run(*g, "--jq", ".data.answer", "ask", "jq 过滤").stdout.strip()
    check("--jq 取值", out.startswith("ECHO[") and "jq 过滤" in out, out[:120])
    r = run(*g, "ask", "截断测试 " * 40, "--max-chars", "60")
    check("--max-chars 截断并附链接", "已截断" in r.stdout and "http" in r.stdout, r.stdout[-120:])
    check("stdin 可用", "来自 stdin" in run(*g, "ask", "-", stdin="来自 stdin").stdout)
    qf = HOME / "q.md"
    qf.write_text("文件里的问题", encoding="utf-8")
    check("--file 可用", "文件里的问题" in run(*g, "ask", "--file", str(qf)).stdout)

    # 多轮：--chat 指定会话（last / 序号 / 裸 id 三种写法）
    d = as_json(run(*g, "--json", "ask", "--chat", "last", "接着聊第二轮"))
    check("--chat last 继续会话", d.get("ok") is True, str(d)[:200])
    check("--chat last 落到真实 chat 页",
          "chat-aaaaaaaa" in str(d.get("data", {}).get("url", "")),
          str(d.get("data", {}).get("url")))
    d2 = as_json(run(*g, "--json", "ask", "--chat", "1", "序号也能用"))
    check("--chat 序号继续会话", d2.get("ok") is True
          and "chat-aaaaaaaa" in str(d2.get("data", {}).get("url", "")), str(d2)[:200])
    d3 = as_json(run(*g, "--json", "ask", "--chat", "chat-bbbbbbbb-0002", "裸 id"))
    check("--chat 裸 id 继续会话", d3.get("ok") is True
          and "chat-bbbbbbbb-0002" in str(d3.get("data", {}).get("url", "")), str(d3)[:200])
    d4 = as_json(run(*g, "--json", "ask", "--chat", "1", "dryrun", "--dry-run"))
    check("--dry-run 显示会话目标", str(d4.get("data", {}).get("chat", "")).startswith("1")
          or d4.get("dry_run") is True, str(d4)[:160])

    # 流式：stderr 有中间过程，stdout 仍然只有最终回答
    r = run(*g, "ask", "--stream", "流式输出测试")
    check("--stream 退出码 0", r.returncode == 0, r.stderr[-160:])
    check("--stream stderr 有增量", "ECHO[" in r.stderr, repr(r.stderr[-120:]))
    check("--stream 增量拼起来就是完整回答", r.stderr.strip() == r.stdout.strip(),
          f"stdout={r.stdout[:60]!r} stderr={r.stderr[:60]!r}")
    check("--stream stdout 仍只有回答正文",
          r.stdout.strip().startswith("ECHO[") and "{" not in r.stdout[:2], r.stdout[:120])
    check("--stream 不带 flag 时 stderr 无正文", "ECHO[" not in run(*g, "ask", "安静模式").stderr)
    check("-q 下不流式", "ECHO[" not in run(*g, "-q", "ask", "--stream", "安静流式").stderr)
    d = as_json(run(*g, "--json", "ask", "--stream", "流式+json"))
    check("--stream 与 --json 可共存", d.get("ok") is True
          and d.get("data", {}).get("answer", "").startswith("ECHO["), str(d)[:160])

    # ---------- 7. chat ----------
    print("\n[7] arena chat")
    check("list 读到历史", "用上传的 cookies 提问" in run(*g, "chat", "list").stdout)
    d = as_json(run(*g, "--json", "chat", "list"))
    check("list --json 有 chats 数组", isinstance(d.get("data", {}).get("chats"), list), str(d)[:160])
    check("--jq 取标题", "用上传的 cookies 提问" in run(*g, "--jq", ".data.chats[].title",
                                                     "chat", "list").stdout)
    r = run(*g, "chat", "last")
    check("last 输出 URL", r.stdout.strip().startswith(base) and "/agent/chat-" in r.stdout, r.stdout)
    check("open 序号 2 正确", "chat-bbbbbbbb-0002" in run(*g, "chat", "open", "2").stdout)

    # ---------- 8. profile ----------
    print("\n[8] arena profile")
    check("list 含 default", "default" in run(*g, "profile", "list").stdout)
    check("create --from cookies", run(*g, "profile", "create", "work", "--from", str(ns)).returncode == 0)
    check("work 会话独立", (HOME / "profiles" / "work" / "state.json").exists())
    check("use 成功", run("profile", "use", "work").returncode == 0)
    d = as_json(run("--json", "profile", "show"))
    check("当前 profile = work", d.get("data", {}).get("profile") == "work", str(d)[:160])
    check("work 也能提问", "在 work" in run(*g, "ask", "在 work profile 提问").stdout)

    print("  · 高危操作需要 --yes")
    r = run("profile", "delete", "work")
    check("无 --yes 退出码 1", r.returncode == 1, f"{r.returncode}")
    check("报 confirmation_required", "confirmation_required" in (r.stdout + r.stderr), r.stderr[-160:])
    d = as_json(run("--json", "profile", "delete", "work"))
    check("错误信封 type=usage",
          d.get("ok") is False and d.get("error", {}).get("type") == "usage", str(d)[:200])
    check("delete --yes 成功", run("profile", "delete", "work", "--yes").returncode == 0)
    check("profile 目录已清空", not (HOME / "profiles" / "work").exists())

    # ---------- 9. doctor ----------
    print("\n[9] arena doctor")
    d = as_json(run("--json", "doctor"))
    names = [c["name"] for c in d.get("data", {}).get("checks", [])]
    check("doctor --json 有结构", bool(names), str(d)[:200])
    check("含关键检查项", all(n in names for n in ("playwright", "chromium", "network", "session")),
          str(names))

    # ---------- 10. 风险分级 ----------
    print("\n[10] 风险分级：覆盖登录态需要 --yes")
    r = run(*g, "cookies", "import", str(ns))          # 此时 default 已有健康会话
    check("覆盖登录态无 --yes → 退出码 1", r.returncode == 1, f"{r.returncode}")
    check("提示里给了 --yes 与替代方案", "yes" in r.stderr.lower() and "--profile" in r.stderr,
          r.stderr[-200:])
    check("带 --yes 才允许覆盖", run(*g, "cookies", "import", str(ns), "--yes").returncode == 0)
    d = as_json(run(*g, "--json", "--dry-run", "cookies", "import", str(ns)))
    check("dry-run 下不拦（只预览）", d.get("dry_run") is True, str(d)[:160])

    # ---------- 11. --no-wait + jobs ----------
    print("\n[11] --no-wait 后台任务")
    d = as_json(run(*g, "--json", "--no-wait", "ask", "后台任务测试"))
    job_id = d.get("data", {}).get("job_id", "")
    check("拿到 job_id", d.get("ok") is True and job_id, str(d)[:200])
    check("立刻返回（status=running）", d.get("data", {}).get("status") == "running", str(d)[:160])

    deadline = time.time() + 90
    status = ""
    while time.time() < deadline:
        time.sleep(2)
        sd = as_json(run("--json", "jobs", "status", job_id))
        status = sd.get("data", {}).get("status", "")
        if status in ("done", "failed", "lost"):
            break
    check("任务完成", status == "done", f"status={status}")

    r = run("jobs", "result", job_id)
    check("jobs result 输出回答", "后台任务测试" in r.stdout, r.stdout[:160])
    check("jobs result --jq 可用",
          "后台任务测试" in run("--jq", ".data.answer", "jobs", "result", job_id).stdout)
    check("jobs list 含该任务", job_id in run("jobs", "list").stdout)
    check("jobs logs 有内容", len(run("jobs", "logs", job_id, "--tail", "10").stdout.strip()) > 0)
    d = as_json(run("--json", "jobs", "list"))
    check("jobs list --json", any(j["job_id"] == job_id for j in d.get("data", {}).get("jobs", [])),
          str(d)[:200])
    check("jobs cancel 成功", run("jobs", "cancel", job_id).returncode == 0)

    # ---------- 12. schema ----------
    print("\n[12] arena schema")
    d = as_json(run("--json", "schema"))
    check("列出全部命令", len(d.get("data", {}).get("commands", [])) >= 10, str(d)[:160])
    d = as_json(run("--json", "schema", "ask"))
    info = d.get("data", {})
    check("ask 的风险是 write", info.get("risk") == "write", str(info)[:160])
    check("有参数列表", any(p.get("arg") == "prompt" for p in info.get("params", [])), str(info)[:240])
    d = as_json(run("--json", "schema", "profile", "delete"))
    check("profile delete 标为 high-risk-write",
          d.get("data", {}).get("risk") == "high-risk-write", str(d)[:160])
    r = run("--json", "schema", "nope")
    check("未知命令 → 64", r.returncode == 64, f"{r.returncode}")
    check("给出可用命令提示",
          "unknown_command" in r.stdout and "hint" in r.stdout, r.stdout[:200])
    check("人类可读 schema 输出", "风险" in run("schema", "cookies", "import").stdout)

    # ---------- 13. 错误信封 ----------
    print("\n[13] 统一错误信封")
    d = as_json(run("--json", "--base-url", "http://127.0.0.1:1", "ask", "x"))
    err = d.get("error", {})
    check("ok=false 且带 type/subtype/message/hint",
          d.get("ok") is False and all(k in err for k in ("type", "subtype", "message", "hint")),
          str(d)[:240])
    r = run("--json", "--base-url", base, "--profile", "no-such-profile", "ask", "x")
    d = as_json(r)
    check("无会话 profile → auth.login_required",
          d.get("error", {}).get("subtype") in ("login_required", "browser_error"),
          str(d)[:240])

    srv.shutdown()
    shutil.rmtree(HOME, ignore_errors=True)
    print("\n" + ("全部通过 ✅" if not FAILED else f"失败 {len(FAILED)} 项 ❌: {FAILED}"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
