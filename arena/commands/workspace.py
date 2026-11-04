"""arena workspace — conversation workspace: list files / view contents / download.

The Workspace card on the right side of the conversation page holds the files the Agent produced (plus user uploads). The three commands here
all rely on the browser (with the login session) to click and read, without guessing at APIs:

    arena workspace list                 # List files (automatically expands all folders)
    arena workspace show demo.txt        # Print text contents (stdout contains only the content)
    arena workspace download demo.txt    # Save locally (--out specifies a path/directory; --all packages the entire workspace)
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
    """If --chat is omitted, use the most recent conversation; if there is no history at all, fall back to the current (new) conversation."""
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
        "hint": "arena workspace show <path> to view contents; arena workspace download <path> to save locally",
    }


def run_list(ctx, args) -> int:
    listing = _listing(ctx, args)
    data = _files_payload(listing)
    stats = data["stats"]

    def human() -> None:
        used = f"{stats.get('used') or '?'}/{stats.get('quota') or '?'}"
        n = f"{data['count']}/{stats.get('file_limit') or '?'} files"
        rows = [[f["path"], human_size(f.get("size"))] for f in data["files"]] or [["(workspace is empty)", ""]]
        ctx.ui.table(["path", "size"], rows,
                     title=f"Workspace ({used}  {n}) · chat {data['chat_id'] or '-'}")
        if data["source"] == "archive":
            ctx.ui.info("(The file list comes from the zip package of \"Download workspace\"; this conversation's file-tree UI could not be read row by row)")
        if data["truncated"]:
            ctx.ui.warn("Too many folders; only some were expanded (add --no-expand to view the top level)")
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
            f"{got['path']} appears to be a binary file ({human_size(got['size'])}, {got['content_type'] or 'unknown type'})",
            f"Download it first with arena workspace download {got['path']} --out <local path>, then view it",
            exit_code=1,
        )

    text = data_bytes.decode("utf-8", errors="replace")
    shown = text
    if limit and len(text) > limit:
        shown = text[:limit].rstrip() + f"\n\n…(truncated; {len(text)} characters in total; use --max-chars 0 to see the full text)"

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
            print(f"[file] {got['path']}  {human_size(got['size'])}  sha256={sha256_bytes(data_bytes)[:12]}…",
                  file=__import__("sys").stderr)

    return ctx.emit(data, human=human)


def run_download(ctx, args) -> int:
    download_all = bool(getattr(args, "all", False))
    ref = normalize_ref(getattr(args, "path", "") or "")
    if not download_all and not ref:
        raise ArenaError("usage", "missing_argument", "A file path is required",
                         "arena workspace download demo.txt --out ./demo.txt"
                         "(or use --all to package the entire workspace)", exit_code=64)

    with open_session(ctx) as session:
        chat = _resolve(session, _chat_ref(args))
        got = session.workspace_archive(chat) if download_all else session.workspace_file(chat, ref)

    name = "workspace.zip" if download_all else got["name"]
    out = safe_target(getattr(args, "out", None), name)
    if out.exists() and not (getattr(args, "yes", False) or getattr(args, "force", False)):
        raise confirmation(
            f"overwrite local file {out}",
            f"To confirm the overwrite: arena workspace download … --out {out} --yes; or use a different --out",
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(got["bytes"])
    try:
        out.chmod(0o600)
    except OSError:
        pass

    data = {
        "chat_id": got["chat_id"],
        "path": "(entire workspace)" if download_all else got["path"],
        "out": str(out),
        "size": got["size"],
        "sha256": sha256_bytes(got["bytes"]),
        "content_type": got["content_type"],
    }

    def human() -> None:
        ctx.ui.ok(f"Saved {out} ({human_size(data['size'])})")
        ctx.ui.kv([("source", data["path"]),
                   ("chat", data["chat_id"] or "-"),
                   ("sha256", data["sha256"][:16] + "…")])

    return ctx.emit(data, human=human)
