"""Background jobs (`arena ask --no-wait`): run long tasks in the background and retrieve the results later using the job id.

Follows lark-cli's two-step pattern (--no-wait / retrieve later): an agent does not have to block for minutes while the browser finishes.

Directory layout:
    ~/.arena-cli/jobs/<job_id>/
        job.json      # status, prompt, profile, pid
        result.json   # result after completion (same structure as the data from arena ask --json)
        run.log       # engine log
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

STALE_SECONDS = 2.0   # Allow a buffer even after the pid is gone, to avoid misjudging a job that was just spawned and checked immediately


# --------------------------------------------------------------------------- Storage
def jobs_root(home: Path) -> Path:
    root = home / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- Query
def job_dirs(home: Path) -> list[Path]:
    return sorted((p for p in jobs_root(home).iterdir() if p.is_dir()), reverse=True)


def resolve(home: Path, ref: str) -> Path:
    """Supports the full id, a prefix, or last."""
    dirs = job_dirs(home)
    if not dirs:
        raise FileNotFoundError("No background jobs yet")
    if ref in ("last", "latest", "-1"):
        return dirs[0]
    exact = home / "jobs" / ref
    if exact.is_dir():
        return exact
    matches = [d for d in dirs if d.name.startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"Job not found: {ref}")
    raise ValueError(f"Prefix {ref} matches multiple jobs; please provide more characters")


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def status_of(job_dir: Path) -> dict[str, Any]:
    job = _read_json(job_dir / "job.json") or {}
    result = _read_json(job_dir / "result.json")
    status = job.get("status", "unknown")
    if status == "running":
        if result:
            status = "done" if result.get("ok") else "failed"
        elif not pid_alive(job.get("pid")) and time.time() - job.get("created_ts", 0) > STALE_SECONDS:
            status = "lost"
    out = {
        "job_id": job_dir.name,
        "status": status,
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
        "profile": job.get("profile"),
        "prompt": (job.get("prompt") or "")[:200],
        "pid": job.get("pid"),
        "elapsed_seconds": job.get("elapsed_seconds"),
    }
    if result:
        out["ok"] = bool(result.get("ok"))
        if result.get("ok"):
            out["answer"] = result.get("answer", "")
            out["url"] = result.get("url")
        else:
            out["error"] = result.get("error")
    return out


def list_jobs(home: Path, limit: int = 20) -> list[dict[str, Any]]:
    return [status_of(d) for d in job_dirs(home)[:limit]]


def logs(job_dir: Path, tail: int = 40) -> str:
    path = job_dir / "run.log"
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-tail:])


def cancel(job_dir: Path) -> bool:
    job = _read_json(job_dir / "job.json") or {}
    pid = job.get("pid")
    killed = False
    if pid_alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)   # Also terminate the browser process along with it
            killed = True
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
                killed = True
            except OSError:
                pass
    job["status"] = "cancelled"
    job["finished_at"] = _now()
    _write_json(job_dir / "job.json", job)
    return killed


# --------------------------------------------------------------------------- Create
def create(home: Path, prompt: str, *, profile: str, state_file: Path, profile_dir: Path,
           options: dict[str, Any]) -> dict[str, Any]:
    job_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(2).hex()}"
    job_dir = jobs_root(home) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    job = {
        "job_id": job_id,
        "status": "running",
        "created_at": _now(),
        "created_ts": time.time(),
        "profile": profile,
        "prompt": prompt,
        "state_file": str(state_file),
        "profile_dir": str(profile_dir),
        "options": options,
        "pid": None,
    }
    _write_json(job_dir / "job.json", job)

    env = dict(os.environ)
    env["ARENA_HOME"] = str(home)
    repo_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = repo_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    log = open(job_dir / "run.log", "ab", buffering=0)   # noqa: SIM115 — handed over to the child process
    proc = subprocess.Popen(
        [sys.executable, "-m", "arena.jobs", "--run", str(job_dir)],
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True, env=env, cwd=repo_root,
    )
    job["pid"] = proc.pid
    _write_json(job_dir / "job.json", job)
    return {"job_id": job_id, "status": "running", "pid": proc.pid,
            "job_dir": str(job_dir), "profile": profile}


# --------------------------------------------------------------------------- worker
def run_worker(job_dir: Path) -> int:
    """Background process entry point: read job.json, run one ask, write result.json."""
    job = _read_json(job_dir / "job.json") or {}
    if not job:
        return 1
    from .config import profile_paths  # noqa: F401  (keeps dependencies explicit)
    from .engine import Session, SkillError, load_config

    opts = job.get("options") or {}
    cfg = load_config(None, {
        "base_url": opts.get("base_url"),
        "wait_timeout": opts.get("timeout"),
        "stable_seconds": opts.get("stable"),
    })
    started = time.time()
    try:
        progress = {"logged": 0}

        def on_update(text: str) -> None:      # Progress is written to run.log, visible via `arena jobs logs`
            if len(text) - progress["logged"] >= 200:
                progress["logged"] = len(text)
                print(f"[progress] {len(text)} characters generated so far", flush=True)

        with Session(cfg, Path(job["profile_dir"]), Path(job["state_file"]),
                     headed=bool(opts.get("headed"))) as session:
            chat = opts.get("chat")
            if chat:
                from .commands.ask import resolve_chat   # Deferred import to avoid a circular dependency
                chat = resolve_chat(session, chat)
            result = session.ask(job["prompt"], timeout=opts.get("timeout"),
                                 stable=opts.get("stable"), chat=chat,
                                 on_update=on_update)
        result["elapsed_seconds"] = round(time.time() - started, 1)
        _write_json(job_dir / "result.json", result)
        job.update(status="done", finished_at=_now(), elapsed_seconds=result["elapsed_seconds"])
    except SkillError as exc:
        _write_json(job_dir / "result.json",
                    {"ok": False, "code": exc.code, "error": exc.message, "prompt": job.get("prompt")})
        job.update(status="failed", finished_at=_now(),
                   elapsed_seconds=round(time.time() - started, 1))
    except Exception as exc:  # noqa: BLE001
        _write_json(job_dir / "result.json",
                    {"ok": False, "code": "INTERNAL", "error": f"{type(exc).__name__}: {exc}"})
        job.update(status="failed", finished_at=_now(),
                   elapsed_seconds=round(time.time() - started, 1))
    _write_json(job_dir / "job.json", job)
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--run":
        sys.exit(run_worker(Path(sys.argv[2])))
    sys.exit("Usage: python -m arena.jobs --run <job_dir>")
