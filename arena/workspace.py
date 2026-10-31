"""workspace —— arena.ai 会话工作区（Workspace 面板）的纯逻辑。

页面结构（2026-10 实测，`/agent/<chat_id>` 右侧 Workspace 卡）：

    <div class="... max-h-[400px] ...">
      Workspace                        47B/128.0MB   2/10K files     [⬇ 整个工作区]
      ▸ uploads/            button[aria-label="Collapse uploads folder"]  style="padding-left:8px"
          test_upload.txt   div[role=button][aria-label="test_upload.txt file"]  style="padding-left:24px"
                            └ a[aria-label="Download test_upload.txt"][href="/api/chat/workspace/cas/user/<hash>"][download="test_upload.txt"]
      demo.txt              div[role=button][aria-label="demo.txt file"]  style="padding-left:8px"

层级靠 `padding-left` 表示（8 = 根，24 = 一层，40 = 两层…）；折叠的文件夹子项**不在 DOM 里**，
所以想列全就得把 "Expand … folder" 逐个点开。
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".htm",
    ".css", ".scss", ".sh", ".bash", ".zsh", ".sql", ".xml", ".svg", ".log", ".env",
    ".gitignore", ".dockerfile", ".makefile", ".r", ".go", ".rs", ".java", ".kt", ".c",
    ".h", ".cpp", ".hpp", ".rb", ".php", ".lua", ".vue", ".svelte",
}

BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".zip", ".gz", ".tar",
    ".mp3", ".mp4", ".mov", ".wav", ".m4a", ".xlsx", ".docx", ".pptx", ".woff", ".woff2",
}


def build_paths(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 DOM 顺序 + depth 把「文件名」拼成「相对路径」。

    输入：`[{"kind": "folder"|"file", "name": ..., "depth": 0, "href": ...}]`
    输出：同样的条目，但多一个 `path`（如 `uploads/test_upload.txt`）。
    """
    stack: list[str] = []
    out: list[dict[str, Any]] = []
    for raw in entries:
        depth = int(raw.get("depth") or 0)
        name = (raw.get("name") or "").strip()
        if not name:
            continue
        stack = stack[:depth]
        item = dict(raw)
        if raw.get("kind") == "folder":
            stack.append(name)
            item["path"] = "/".join(stack) + "/"
        else:
            item["path"] = "/".join([*stack, name])
            item["name"] = name
        out.append(item)
    return out


def files_only(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if e.get("kind") == "file"]


def normalize_ref(ref: str) -> str:
    """把用户写的路径规整一下：去掉开头的 `./`、`/`、结尾多余斜杠。"""
    ref = (ref or "").strip().replace("\\", "/")
    while ref.startswith("./"):
        ref = ref[2:]
    ref = ref.lstrip("/")
    return ref.rstrip("/")


def find_file(entries: Iterable[dict[str, Any]], ref: str) -> Optional[dict[str, Any]]:
    """按路径找文件，容忍几种常见写法：

    * `uploads/test_upload.txt`（完整路径，最推荐）
    * `test_upload.txt`（只给文件名：唯一时直接用）
    * `*/test_upload.txt`（通配前缀）
    """
    ref = normalize_ref(ref)
    files = files_only(entries)
    for f in files:
        if f["path"] == ref:
            return f
    if "*" in ref:
        pattern = re.compile("^" + re.escape(ref).replace(r"\*", "[^/]*") + "$")
        hits = [f for f in files if pattern.match(f["path"])]
        if len(hits) == 1:
            return hits[0]
    hits = [f for f in files if f["path"].endswith("/" + ref) or f["name"] == ref]
    if len(hits) == 1:
        return hits[0]
    return None


def ambiguous_candidates(entries: Iterable[dict[str, Any]], ref: str) -> list[str]:
    ref = normalize_ref(ref)
    return [f["path"] for f in files_only(entries)
            if f["path"].endswith("/" + ref) or f["name"] == ref]


def is_probably_text(name: str, sample: bytes = b"") -> bool:
    suffix = Path(name).suffix.lower()
    if suffix in BINARY_SUFFIXES:
        return False
    if suffix in TEXT_SUFFIXES:
        return True
    if sample:
        if b"\x00" in sample:
            return False
        try:
            sample.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False
    return True


def human_size(n: Optional[int]) -> str:
    if n is None:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def safe_target(out: Optional[str], name: str, cwd: Optional[Path] = None) -> Path:
    """`--out` 可以是文件、也可以是已存在的目录（那就拼上文件名）。"""
    base = Path(cwd or Path.cwd())
    if not out:
        return base / Path(name).name
    target = Path(out).expanduser()
    if not target.is_absolute():
        target = base / target
    if target.is_dir() or str(out).endswith(os.sep) or out.endswith("/"):
        return target / Path(name).name
    return target


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_stats(stat_lines: Iterable[str]) -> dict[str, Any]:
    """从面板头部的 `47B/128.0MB`、`2/10K files` 两行里抠出数字（抠不到就留空）。"""
    used = total = files = None
    files_label: Optional[str] = None
    file_limit: Optional[str] = None
    for line in stat_lines:
        line = (line or "").strip()
        if not line:
            continue
        m = re.search(r"([\d.]+)\s*([KMG]?B)\s*/\s*([\d.]+)\s*([KMG]?B)", line, re.I)
        if m:
            used = f"{m.group(1)}{m.group(2)}"
            total = f"{m.group(3)}{m.group(4)}"
        m = re.search(r"([\d.]+)\s*([KMG]?)\s*/\s*([\d.]+\s*[KMG]?)\s*files?", line, re.I)
        if m:
            mult = {"": 1, "K": 1000, "M": 1_000_000, "G": 1_000_000_000}[m.group(2).upper()]
            files = int(float(m.group(1)) * mult)
            files_label = f"{m.group(1)}{m.group(2)}"
            file_limit = m.group(3).replace(" ", "")
    return {"used": used, "quota": total, "files": files, "files_label": files_label,
            "file_limit": file_limit}
