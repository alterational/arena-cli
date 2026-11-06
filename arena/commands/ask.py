"""arena ask — send content to the Arena Agent and retrieve the result (supports --dry-run / --no-wait)."""

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
    tail = f"\n\n…(truncated; {len(answer)} characters in total)"
    if url:
        tail += f"\nFull content: {url}"
    return answer[:limit].rstrip() + tail


def resolve_chat(session, ref: str) -> str:
    """Resolve `last` / an index (#1 in arena chat list) into the real chat_id.

    open_chat() only accepts chat_id / URL; passing "last" directly would be treated as an id and produce /agent/last.
    The Chinese alias "最新" (meaning "latest") is still accepted for backward compatibility.
    """
    ref = (ref or "").strip()
    if ref in ("last", "最新", "-1"):  # "最新" = Chinese alias for "latest", kept for compatibility
        chats = session.list_chats(limit=1)
        if not chats:
            from ..errors import ArenaError
            raise ArenaError("validation", "not_found", "No conversation history to continue",
                             "Send a message without --chat first, or run arena chat list to see what exists",
                             exit_code=1)
        return chats[0]["chat_id"]
    if ref.isdigit():
        n = int(ref)
        chats = session.list_chats(limit=max(n, 20))
        if not 1 <= n <= len(chats):
            from ..errors import ArenaError
            raise ArenaError("validation", "not_found",
                             f"Index {n} is out of range (only {len(chats)} conversations)",
                             "Run arena chat list to see the indexes", exit_code=1)
        return chats[n - 1]["chat_id"]
    return ref


class Streamer:
    """Writes each newly extracted "full current text" increment to stderr (stdout still contains only the final answer).

    Note: open_session() redirects sys.stderr to a StringIO in non-verbose mode (to swallow
    Playwright noise), so this must write to sys.__stderr__; otherwise the increments would be swallowed as well.
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
        if text.startswith(self.written):          # Normal case: the text only grows
            delta = text[len(self.written):]
        else:                                      # Re-rendered or rewritten: print the whole thing again
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
        raise ArenaError("validation", "invalid_argument", "No content was received to send to the Agent",
                         'Usage: arena ask "your question" / arena ask --file q.md / echo question | arena ask -',
                         exit_code=1)

    chat = getattr(args, "chat", None)

    # ---- --dry-run: preview only; do not launch the browser ----
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
            "would": ["open arena.ai/agent", "type into the input box and submit", "wait for generation to stabilize",
                      "retrieve the answer" + (" (printing increments to stderr along the way)" if getattr(args, "stream", False) else "")],
        }
        if not data["session_ready"]:
            data["warning"] = "No usable login session; the real run will fail with auth.login_required"
        return ctx.emit(data, dry_run=True, human=lambda: (
            ctx.ui.kv([("action", "ask (not actually executed)"),
                       ("profile", data["profile"]),
                       ("target", f"{data['base_url']}/agent"),
                       ("conversation", str(data["chat"])),
                       ("timeout", f"{data['timeout']}s"),
                       ("prompt", f"{data['prompt_chars']} characters: {data['prompt_preview'][:60]}…"),
                       ("login session", "ready" if data["session_ready"] else "missing")],
                      title="[dry-run] arena ask"),
            ctx.ui.info("→ " + " → ".join(data["would"])),
        ))

    # ---- --no-wait: run in the background; return job_id immediately ----
    if getattr(args, "no_wait", False):
        job = jobs.create(
            ctx.paths.home, prompt, profile=ctx.paths.profile,
            state_file=ctx.paths.state_file, profile_dir=ctx.paths.profile_dir,
            options={"engine_config": ctx.engine_cfg, "base_url": ctx.get("base_url"), "timeout": _timeout(ctx, args),
                     "stable": _stable(ctx, args), "chat": chat,
                     "headed": bool(getattr(args, "headed", False))},
        )
        data = {**job, "hint": f"arena jobs result {job['job_id']} (or arena jobs logs {job['job_id']} to check progress)"}
        return ctx.emit(data, human=lambda: (
            ctx.ui.ok(f"Moved to background: {job['job_id']} (pid {job['pid']})"),
            ctx.ui.info("Get the result: arena jobs result " + job["job_id"]),
            ctx.ui.info("Check progress: arena jobs logs " + job["job_id"] + "; arena jobs list to see all"),
        ))

    # ---- Normal execution ----
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
            print(f"\n[url] {result.get('url')}  [elapsed] {result.get('elapsed_seconds')}s",
                  file=sys.stderr)

    data = dict(result)
    data["answer"] = shown
    data["max_chars_applied"] = int(limit or 0)
    return ctx.emit(data, human=human)
