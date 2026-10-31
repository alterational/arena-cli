"""arena workspace —— 会话工作区：列文件 / 看内容 / 下载。

会话页右侧那张 Workspace 卡就是 Agent 干活时产出的文件（也有用户上传件）。这里的三条命令
都靠浏览器（带着登录态）去点、去读，不猜接口：

    arena workspace list                 # 列文件（自动展开所有文件夹）
    arena workspace show demo.txt        # 打印文本内容（stdout 只有内容）
    arena workspace download demo.txt    # 存到本地（--out 指定路径/目录，--all 打包整个工作区）
"""

from __future__ import annotations

from pathlib import Path

from ..errors import ArenaError, confirmation
from ..session import open_session
from ..workspace import (
    files_only,
    human_size,
    is_probably_text,
    normalize_ref,
    safe_target,
    sha256_bytes,
)


def _chat_ref(args) -> str | None:
    return getattr(args, "chat", None)


def _resolve(session, ref: str | None) -> str | None:
    """--chat 缺省时用最近一个会话；没有任何历史就退回当前（新）会话。"""
    from .ask import resolve_chat

    try:
        return resolve_chat(session, ref or "last")
    except ArenaError:
        return None


def _listing(ctx, args) -> dict:
    with open_session(ctx) as session:
        chat = _resolve(session, _chat_ref(args))
        return session.workspace_entries(chat, expand=not getattr(args, "no_expand", False))


def _files_payload(listing: dict) -> dict:
    files = [{k: f.get(k) for k in ("path", "name", "size", "href")}
             for f in files_only(listing["entries"])]
    folders = [e["path"] for e in listing["entries"] if e.get("kind") == "folder"]
    return {
        "chat_id": listing["chat_id"],
        "stats": listing["stats"],
        "count": len(files),
        "files": files,
        "folders": folders,
        "truncated": listing.get("truncated", False),
        "source": listing.get("source", "dom"),
        "hint": "arena workspace show <path> 查看内容；arena workspace download <path> 存到本地",
    }


def run_list(ctx, args) -> int:
    listing = _listing(ctx, args)
    data = _files_payload(listing)
    stats = data["stats"]

    def human() -> None:
        used = f"{stats.get('used') or '?'}/{stats.get('quota') or '?'}"
        n = f"{data['count']}/{stats.get('file_limit') or '?'} files"
        rows = [[f["path"], human_size(f.get("size"))] for f in data["files"]] or [["(工作区是空的)", ""]]
        ctx.ui.table(["path", "大小"], rows,
                     title=f"Workspace（{used}  {n}）· chat {data['chat_id'] or '-'}")
        if data["source"] == "archive":
            ctx.ui.info("（文件清单来自「下载工作区」的 zip 包，这个会话的文件树 UI 读不出行）")
        if data["truncated"]:
            ctx.ui.warn("文件夹太多，只展开了一部分（可加 --no-expand 看顶层）")
        ctx.ui.info(data["hint"])

    return ctx.emit(data, human=human)


def run_show(ctx, args) -> int:
    ref = normalize_ref(args.path)
    with open_session(ctx) as session:
        chat = _resolve(session, _chat_ref(args))
        got = session.workspace_file(chat, ref)

    data_bytes: bytes = got["bytes"]
    text_ok = is_probably_text(got["name"], data_bytes[:4096])
    limit = getattr(args, "max_chars", None)
    if limit is None:
        limit = ctx.get("max_chars", 0)
    limit = int(limit or 0)

    if not text_ok:
        raise ArenaError(
            "validation", "not_text_file",
            f"{got['path']} 看起来是二进制文件（{human_size(got['size'])}，{got['content_type'] or '未知类型'}）",
            f"用 arena workspace download {got['path']} --out <本地路径> 下载后再看",
            exit_code=1,
        )

    text = data_bytes.decode("utf-8", errors="replace")
    shown = text
    if limit and len(text) > limit:
        shown = text[:limit].rstrip() + f"\n\n…（已截断，共 {len(text)} 字，可用 --max-chars 0 看全文）"

    data = {
        "chat_id": got["chat_id"],
        "path": got["path"],
        "size": got["size"],
        "content_type": got["content_type"],
        "chars": len(text),
        "truncated": shown != text,
        "content": shown,
    }

    def human() -> None:
        print(shown, end="" if shown.endswith("\n") else "\n")
        if ctx.ui.verbose:
            print(f"[文件] {got['path']}  {human_size(got['size'])}  sha256={sha256_bytes(data_bytes)[:12]}…",
                  file=__import__("sys").stderr)

    return ctx.emit(data, human=human)


def run_download(ctx, args) -> int:
    download_all = bool(getattr(args, "all", False))
    ref = normalize_ref(getattr(args, "path", "") or "")
    if not download_all and not ref:
        raise ArenaError("usage", "missing_argument", "要给一个文件路径",
                         "arena workspace download demo.txt --out ./demo.txt"
                         "（或 --all 打包整个工作区）", exit_code=64)

    with open_session(ctx) as session:
        chat = _resolve(session, _chat_ref(args))
        got = session.workspace_archive(chat) if download_all else session.workspace_file(chat, ref)

    name = "workspace.zip" if download_all else got["name"]
    out = safe_target(getattr(args, "out", None), name)
    if out.exists() and not (getattr(args, "yes", False) or getattr(args, "force", False)):
        raise confirmation(
            f"覆盖本地文件 {out}",
            f"确认覆盖：arena workspace download … --out {out} --yes；或换个 --out",
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(got["bytes"])
    try:
        out.chmod(0o600)
    except OSError:
        pass

    data = {
        "chat_id": got["chat_id"],
        "path": "（整个工作区）" if download_all else got["path"],
        "out": str(out),
        "size": got["size"],
        "sha256": sha256_bytes(got["bytes"]),
        "content_type": got["content_type"],
    }

    def human() -> None:
        ctx.ui.ok(f"已保存 {out}（{human_size(data['size'])}）")
        ctx.ui.kv([("来源", data["path"]),
                   ("chat", data["chat_id"] or "-"),
                   ("sha256", data["sha256"][:16] + "…")])

    return ctx.emit(data, human=human)
