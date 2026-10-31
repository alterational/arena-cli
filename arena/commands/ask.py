"""arena ask —— 把内容交给 Arena Agent 并取回结果（支持 --dry-run / --no-wait）。"""

from __future__ import annotations

import sys
from pathlib import Path

from .. import jobs
from ..session import open_session


def read_prompt(args) -> str:
    if getattr(args, "file", None):
        return Path(args.file).expanduser().read_text(encoding="utf-8")
    words = args.prompt or []
    if not words or words == ["-"]:
        return sys.stdin.read()
    return " ".join(words)


def trim(answer: str, url: str | None, limit: int) -> str:
    if not limit or len(answer) <= limit:
        return answer
    tail = f"\n\n…（已截断，共 {len(answer)} 字）"
    if url:
        tail += f"\n完整内容：{url}"
    return answer[:limit].rstrip() + tail


def resolve_chat(session, ref: str) -> str:
    """把 `last` / 序号（arena chat list 里的 #1）解析成真正的 chat_id。

    open_chat() 只认 chat_id / URL，直接传 "last" 会被当成 id 拼成 /agent/last。
    """
    ref = (ref or "").strip()
    if ref in ("last", "最新", "-1"):
        chats = session.list_chats(limit=1)
        if not chats:
            from ..errors import ArenaError
            raise ArenaError("validation", "not_found", "没有历史会话可以接着聊",
                             "先不指定 --chat 发一条，或者 arena chat list 看看有哪些",
                             exit_code=1)
        return chats[0]["chat_id"]
    if ref.isdigit():
        n = int(ref)
        chats = session.list_chats(limit=max(n, 20))
        if not 1 <= n <= len(chats):
            from ..errors import ArenaError
            raise ArenaError("validation", "not_found",
                             f"序号 {n} 超出范围（只有 {len(chats)} 条）",
                             "arena chat list 看序号", exit_code=1)
        return chats[n - 1]["chat_id"]
    return ref


class Streamer:
    """把每次抽到的「当前完整文本」增量打到 stderr（stdout 仍只留最终回答）。

    注意：open_session() 在非 verbose 模式会把 sys.stderr 重定向到 StringIO（为了吞掉
    Playwright 的噪音），所以这里必须写 sys.__stderr__，否则增量会被一起吞掉。
    """

    def __init__(self, ui) -> None:
        self.ui = ui
        self.written = ""
        self.enabled = True

    @staticmethod
    def _err():
        return sys.__stderr__ or sys.stderr

    def __call__(self, text: str) -> None:
        if not self.enabled or self.ui.quiet:
            return
        if text.startswith(self.written):          # 正常情况：文本只会变长
            delta = text[len(self.written):]
        else:                                      # 重新渲染/改写了，重新打一份
            delta = ("\n" if self.written else "") + text
        self.written = text
        if delta:
            err = self._err()
            err.write(delta)
            err.flush()

    def close(self) -> None:
        if self.enabled and self.written and not self.ui.quiet:
            err = self._err()
            err.write("\n")
            err.flush()
        self.enabled = False


def _timeout(ctx, args):
    return getattr(args, "timeout", None) or ctx.get("timeout")


def _stable(ctx, args):
    return getattr(args, "stable", None) or ctx.get("stable")


def run(ctx, args) -> int:
    prompt = (read_prompt(args) or "").strip()
    if not prompt:
        from ..errors import ArenaError
        raise ArenaError("validation", "invalid_argument", "没有收到要发给 Agent 的内容",
                         '用法：arena ask "你的问题" / arena ask --file q.md / echo 问题 | arena ask -',
                         exit_code=1)

    chat = getattr(args, "chat", None)

    # ---- --dry-run：只预览，不开浏览器 ----
    if ctx.dry_run():
        from ..commands.admin import _session_summary
        session = _session_summary(ctx.paths.state_file, ctx.engine_cfg)
        data = {
            "action": "ask",
            "profile": ctx.paths.profile,
            "base_url": ctx.get("base_url"),
            "chat": chat or "(new chat)",
            "timeout": _timeout(ctx, args),
            "stable": _stable(ctx, args),
            "prompt_chars": len(prompt),
            "prompt_preview": prompt[:200],
            "state_file": str(ctx.paths.state_file),
            "session_ready": bool(session.get("has_auth_cookie")),
            "stream": bool(getattr(args, "stream", False)),
            "would": ["打开 arena.ai/agent", "写入输入框并提交", "等待生成稳定",
                      "取回回答" + ("（过程中把增量打到 stderr）" if getattr(args, "stream", False) else "")],
        }
        if not data["session_ready"]:
            data["warning"] = "没有可用登录态，真正执行时会报 auth.login_required"
        return ctx.emit(data, dry_run=True, human=lambda: (
            ctx.ui.kv([("操作", "ask（不会真的执行）"),
                       ("profile", data["profile"]),
                       ("目标", f"{data['base_url']}/agent"),
                       ("会话", str(data["chat"])),
                       ("超时", f"{data['timeout']}s"),
                       ("prompt", f"{data['prompt_chars']} 字：{data['prompt_preview'][:60]}…"),
                       ("登录态", "可用" if data["session_ready"] else "缺失")],
                      title="[dry-run] arena ask"),
            ctx.ui.info("→ " + " → ".join(data["would"])),
        ))

    # ---- --no-wait：挂后台，立刻返回 job_id ----
    if getattr(args, "no_wait", False):
        job = jobs.create(
            ctx.paths.home, prompt, profile=ctx.paths.profile,
            state_file=ctx.paths.state_file, profile_dir=ctx.paths.profile_dir,
            options={"base_url": ctx.get("base_url"), "timeout": _timeout(ctx, args),
                     "stable": _stable(ctx, args), "chat": chat,
                     "headed": bool(getattr(args, "headed", False))},
        )
        data = {**job, "hint": f"arena jobs result {job['job_id']}（或 arena jobs logs {job['job_id']} 看进度）"}
        return ctx.emit(data, human=lambda: (
            ctx.ui.ok(f"已挂到后台：{job['job_id']}（pid {job['pid']}）"),
            ctx.ui.info("取结果：arena jobs result " + job["job_id"]),
            ctx.ui.info("看进度：arena jobs logs " + job["job_id"] + "；arena jobs list 查看全部"),
        ))

    # ---- 正常执行 ----
    streamer = Streamer(ctx.ui) if getattr(args, "stream", False) else None
    with open_session(ctx) as session:
        if chat:
            chat = resolve_chat(session, chat)
        result = session.ask(prompt, timeout=_timeout(ctx, args),
                             stable=_stable(ctx, args), chat=chat,
                             on_update=streamer,
                             poll=0.5 if streamer else None)
    if streamer:
        streamer.close()

    answer = result.get("answer", "")
    limit = getattr(args, "max_chars", None)
    if limit is None:
        limit = ctx.get("max_chars", 0)
    shown = trim(answer, result.get("url"), int(limit or 0))

    def human() -> None:
        print(shown)
        if ctx.ui.verbose:
            print(f"\n[url] {result.get('url')}  [耗时] {result.get('elapsed_seconds')}s",
                  file=sys.stderr)

    data = dict(result)
    data["answer"] = shown
    data["max_chars_applied"] = int(limit or 0)
    return ctx.emit(data, human=human)
