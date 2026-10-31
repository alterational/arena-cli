#!/usr/bin/env python3
"""
run_entry_tests.py —— 验证「Hermes 技能入口」这一层

覆盖：
  1. arena_ask.py 成功时 stdout **只有回答正文**（没有 JSON、没有引擎日志、没有 UI 噪音）
  2. 失败时输出一行 `❌ CODE: 建议`，退出码可被 Hermes 识别（2 = 需要登录）
  3. --cookies 一步到位（导入 + 提问）
  4. --json 结构化字段齐全
  5. --max-chars 截断并附会话链接
  6. install.sh 把技能装进 HERMES_HOME，装完 verify_skill.py 仍然合规
  7. SKILL.md frontmatter/章节合规（scripts/verify_skill.py）
  8. 数据目录从 ~/.arena-agent 迁到 ~/.arena-cli（只做一次，登录态不丢）
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

    # ---------- 1. 未登录：失败也要"一句话" ----------
    print("[1] 没有会话时：一行错误 + 退出码 2")
    r = run([*common, "--profile-dir", str(tmp / "anon"), "你好"])
    check("退出码 = 2", r.returncode == 2, f"got {r.returncode}: {r.stdout!r}")
    check("stdout 只有一行 ❌ 提示", r.stdout.strip().count("\n") == 0, r.stdout)
    check("提示里带 LOGIN_REQUIRED", "LOGIN_REQUIRED" in r.stdout, r.stdout)
    check("提示里告诉用户下一步（cookies）", "cookies" in r.stdout.lower(), r.stdout)

    # ---------- 2. 上传 cookies：一步到位 ----------
    print("\n[2] --cookies 一步到位（导入 + 提问）")
    ns = tmp / "cookies.txt"
    ns.write_text("# Netscape HTTP Cookie File\n\n"
                  "127.0.0.1\tFALSE\t/\tFALSE\t4102444800\tmock_session\t1\n", encoding="utf-8")
    r = run([*common, "--profile-dir", str(tmp / "p1"), "--cookies", str(ns),
             "帮我总结一下今天的三个待办"])
    answer = r.stdout.strip()
    check("退出码 = 0", r.returncode == 0, f"{r.returncode}: {r.stdout!r} / {r.stderr[-300:]}")
    check("stdout 就是回答正文", answer.startswith("ECHO[") and "帮我总结一下今天的三个待办" in answer, answer[:200])
    check("没有 JSON 花括号", not answer.startswith("{"), answer[:80])
    check("没有引擎日志混进 stdout", "[arena]" not in answer, answer[:200])
    check("没有页面 UI 噪音", "Was this task successful?" not in answer and "Copy" not in answer, answer[:200])

    # ---------- 3. --json ----------
    print("\n[3] --json 结构化输出")
    r = run([*common, "--profile-dir", str(tmp / "p1"), "--json", "结构化测试"])
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        data = {}
    check("是合法 JSON", isinstance(data, dict) and data.get("ok") is True, r.stdout[:200])
    check("字段齐全", all(k in data for k in ("answer", "url", "chat_id", "elapsed_seconds")), str(data)[:200])

    # ---------- 4. --max-chars ----------
    print("\n[4] --max-chars 长回答截断")
    r = run([*common, "--profile-dir", str(tmp / "p1"), "--max-chars", "40",
             "lorem ipsum " * 30])
    out = r.stdout.strip()
    check("退出码 = 0", r.returncode == 0, r.stdout[:200])
    check("已截断并标注", "已截断" in out, out[-120:])
    check("附了会话链接", "http" in out, out[-160:])

    # ---------- 5. stdin ----------
    print("\n[5] stdin 输入（/arena-cli 后面不写参数时）")
    r = run([*common, "--profile-dir", str(tmp / "p1"), "-"], stdin="来自标准输入的问题")
    check("stdin 生效", r.returncode == 0 and "来自标准输入的问题" in r.stdout, r.stdout[:200])

    # ---------- 6. 安装到 HERMES_HOME ----------
    print("\n[6] install.sh 装进 HERMES_HOME")
    fake_home = tmp / "hermes_home"
    env = {**os.environ, "HERMES_HOME": str(fake_home)}
    r = subprocess.run(["bash", str(ROOT / "install.sh")], capture_output=True, text=True, env=env)
    dest = fake_home / "skills" / "arena-cli"
    check("退出码 = 0", r.returncode == 0, r.stdout[-300:] + r.stderr[-300:])
    check("SKILL.md 已就位", (dest / "SKILL.md").exists())
    check("入口脚本已就位", (dest / "scripts" / "arena_ask.py").exists())
    check("维护手册已就位", (dest / "references" / "troubleshooting.md").exists())
    check("没有把 state.json 打包进去", not list(dest.rglob("state.json")))
    # 装完之后再自检一遍
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_skill.py"), str(ROOT)],
                       capture_output=True, text=True)
    check("verify_skill 合规", r.returncode == 0, r.stdout[-300:])
    # 重复安装应被拒绝
    r2 = subprocess.run(["bash", str(ROOT / "install.sh")], capture_output=True, text=True, env=env)
    check("重复安装被拒绝（需 --force）", r2.returncode != 0, r2.stdout[-200:])

    # ---------- 7. 斜杠命令名 = name 字段 ----------
    print("\n[7] 斜杠命令名与 frontmatter 一致")
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    import yaml  # noqa: WPS433
    fm = yaml.safe_load(text.split("---", 2)[1])
    check("name = arena-cli（即 /arena-cli）", fm.get("name") == "arena-cli", str(fm.get("name")))
    check("声明了 requires_toolsets: terminal",
          "terminal" in (fm.get("metadata", {}).get("hermes", {}).get("requires_toolsets") or []))
    check("description 简短（<= 60）", len(fm.get("description", "")) <= 60,
          f"{len(fm.get('description', ''))} 字符")

    # ---------- 8. 数据目录迁移 ----------
    print("\n[8] ~/.arena-agent → ~/.arena-cli 迁移")
    mig = Path(tmp) / "mig"

    def migrate(home: Path, code: str = "from arena.config import arena_home; print(arena_home())") -> str:
        env2 = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
        return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              env=env2, cwd=str(ROOT)).stdout.strip()

    # 8a. 新目录不存在 → 整目录改名，state.json 原样带走
    (mig / ".arena-agent").mkdir(parents=True)
    (mig / ".arena-agent" / "state.json").write_text('{"cookies": 1}', encoding="utf-8")
    out = migrate(mig)
    check("改了名：返回 ~/.arena-cli", out.endswith(".arena-cli"), out)
    check("state.json 跟着搬过去", (mig / ".arena-cli" / "state.json").read_text() == '{"cookies": 1}')
    check("旧目录消失", not (mig / ".arena-agent").exists())
    check("写了迁移标记", (mig / ".arena-cli" / ".migrated-from-arena-agent").exists())

    # 8b. 新目录已存在（先跑过别的命令）→ 逐项合并，缺的搬过去、同名留更新的
    mig2 = Path(tmp) / "mig2"
    (mig2 / ".arena-cli" / "jobs").mkdir(parents=True)
    (mig2 / ".arena-cli" / "jobs" / "j.json").write_text("{}", encoding="utf-8")
    (mig2 / ".arena-cli" / "config.json").write_text('{"new": true}', encoding="utf-8")
    os.utime(mig2 / ".arena-cli" / "config.json", (1, 1))          # 目标里的更旧
    (mig2 / ".arena-agent" / "prof" / "Default").mkdir(parents=True)
    (mig2 / ".arena-agent" / "state.json").write_text('{"cookies": 2}', encoding="utf-8")
    (mig2 / ".arena-agent" / "config.json").write_text('{"old": true}', encoding="utf-8")
    (mig2 / ".arena-agent" / "prof" / "Default" / "Cookies").write_text("c", encoding="utf-8")
    migrate(mig2)
    check("合并后 state.json 到位", (mig2 / ".arena-cli" / "state.json").exists())
    check("已有的 jobs/ 没被冲掉", (mig2 / ".arena-cli" / "jobs" / "j.json").exists())
    check("缺的 profile 文件搬过去", (mig2 / ".arena-cli" / "prof" / "Default" / "Cookies").exists())
    check("同名文件留更新的那个", (mig2 / ".arena-cli" / "config.json").read_text() == '{"old": true}',
          (mig2 / ".arena-cli" / "config.json").read_text())

    # 8c. 迁移只做一次（第二次不再重扫）
    check("旧目录已搬空并清掉", not (mig2 / ".arena-agent").exists())
    (mig2 / ".arena-agent").mkdir()
    (mig2 / ".arena-agent" / "late.json").write_text("x", encoding="utf-8")
    migrate(mig2)
    check("只迁一次：后放进旧目录的文件不再搬", not (mig2 / ".arena-cli" / "late.json").exists())

    # 8d. 显式 ARENA_HOME 时不迁移
    mig3 = Path(tmp) / "mig3"
    (mig3 / ".arena-agent").mkdir(parents=True)
    env3 = {"HOME": str(mig3), "ARENA_HOME": str(mig3 / "mine"), "PATH": os.environ.get("PATH", "")}
    out = subprocess.run([sys.executable, "-c",
                          "from arena.config import arena_home; print(arena_home())"],
                         capture_output=True, text=True, env=env3, cwd=str(ROOT)).stdout.strip()
    check("ARENA_HOME 优先", out == str(mig3 / "mine"), out)
    check("ARENA_HOME 下不动旧目录", (mig3 / ".arena-agent").exists())

    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("全部通过 ✅" if not FAILED else f"失败 {len(FAILED)} 项 ❌: {FAILED}"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
