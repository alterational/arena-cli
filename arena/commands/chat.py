"""arena chat list|last|open — browse conversation history."""

from __future__ import annotations

import webbrowser

from ..errors import ArenaError
from ..session import open_session


def _resolve_url(ctx, ref: str) -> str:
    base = str(ctx.get("base_url")).rstrip("/")
    ref = ref.strip()
    if ref.startswith("http"):
        return ref
    if ref.startswith("/agent/"):
        return base + ref
    return f"{base}{ctx.get('agent_path')}/{ref}"


def _chats(ctx, limit: int) -> list[dict]:
    with open_session(ctx) as session:
        return session.list_chats(limit=limit)


def run_list(ctx, args) -> int:
    items = _chats(ctx, args.limit)
    data = {"chats": items, "count": len(items), "profile": ctx.paths.profile}

    def human() -> None:
        ctx.ui.table(["#", "chat_id", "title"],
                     [[i + 1, c["chat_id"][:8] + "…", c["title"][:70]]
                      for i, c in enumerate(items)],
                     title=f"Recent conversations (profile \"{ctx.paths.profile}\")")
        ctx.ui.info("arena chat open <index|chat_id|last> to open")

    return ctx.emit(data, human=human)


def run_last(ctx, args) -> int:
    items = _chats(ctx, max(1, args.limit))
    if not items:
        raise ArenaError("validation", "not_found", "Could not read conversation history (you may not be logged in)",
                         "arena check", exit_code=2)
    last = items[0]
    return ctx.emit(last, human=lambda: print(last["url"]))


def run_open(ctx, args) -> int:
    ref = args.ref
    if ref == "last":
        items = _chats(ctx, 1)
        if not items:
            raise ArenaError("validation", "not_found", "No conversation history", "Send one with arena ask first")
        ref = items[0]["chat_id"]
    elif ref.isdigit():                      # Supports the indexes shown by arena chat list
        items = _chats(ctx, 50)
        idx = int(ref) - 1
        if not 0 <= idx < len(items):
            raise ArenaError("validation", "not_found",
                             f"Index out of range ({len(items)} conversations in total)", "arena chat list")
        ref = items[idx]["chat_id"]

    url = _resolve_url(ctx, ref)
    if args.browser:
        webbrowser.open(url)
    return ctx.emit({"url": url, "ref": ref}, human=lambda: print(url))
