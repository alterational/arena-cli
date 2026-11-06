"""arena jobs — background jobs (created by `arena ask --no-wait`)."""

from __future__ import annotations

from .. import jobs


def _resolve(ctx, ref: str):
    return jobs.resolve(ctx.paths.home, ref)


def run_list(ctx, args) -> int:
    items = jobs.list_jobs(ctx.paths.home, limit=args.limit)
    data = {"jobs": items, "count": len(items)}

    def human() -> None:
        ctx.ui.table(["job_id", "status", "elapsed", "prompt"],
                     [[j["job_id"], j["status"],
                       f"{j['elapsed_seconds']}s" if j.get("elapsed_seconds") else "-",
                       (j["prompt"] or "")[:52]] for j in items],
                     title=f"Background jobs (profile \"{ctx.paths.profile}\")")
        if items:
            ctx.ui.info("arena jobs result <job_id|last> to get the result; arena jobs logs <id> to view logs")

    return ctx.emit(data, human=human)


def run_status(ctx, args) -> int:
    item = jobs.status_of(_resolve(ctx, args.ref))
    data = item

    def human() -> None:
        ctx.ui.kv([
            ("job_id", item["job_id"]),
            ("status", item["status"]),
            ("created at", item["created_at"]),
            ("elapsed", f"{item['elapsed_seconds']}s" if item.get("elapsed_seconds") else "-"),
            ("profile", item.get("profile")),
            ("prompt", (item.get("prompt") or "")[:80]),
            ("conversation link", item.get("url")),
        ], title="Job status")
        if item["status"] == "running":
            ctx.ui.info("Still running: check back later with arena jobs result, or arena jobs logs to view progress")
        elif item["status"] == "failed":
            ctx.ui.warn(f"Failed: {(item.get('error') or {}).get('message', '')}")

    return ctx.emit(data, human=human)


def run_result(ctx, args) -> int:
    item = jobs.status_of(_resolve(ctx, args.ref))
    status = item["status"]

    if status == "running":
        data = {"job_id": item["job_id"], "status": status, "ready": False,
                "hint": "The job is still running; try again later, or use arena jobs logs"}
        def human_running() -> None:
            ctx.ui.warn(f"Job {item['job_id']} is still running ({status})")
            ctx.ui.info("Try again later, or use arena jobs logs to view progress")
        return ctx.emit(data, human=human_running)

    data = {"job_id": item["job_id"], "status": status, "ready": True, **item}
    if ctx.ui.json_mode or getattr(ctx.args, "jq", None):
        return ctx.emit(data)

    if status == "done" and item.get("answer"):
        limit = ctx.get("max_chars", 0)
        answer = item["answer"]
        if limit and len(answer) > limit:
            answer = answer[:limit].rstrip() + f"\n\n…(truncated; {len(answer)} characters in total)"
            if item.get("url"):
                answer += f"\nFull content: {item['url']}"
        print(answer)
        return 0

    err = item.get("error") or {}
    ctx.ui.fail(err.get("code", "FAILED"), err.get("error", "Job failed"),
                "Use arena jobs logs to see the cause")
    return 1


def run_logs(ctx, args) -> int:
    job_dir = _resolve(ctx, args.ref)
    text = jobs.logs(job_dir, tail=args.tail)
    if ctx.ui.json_mode:
        return ctx.emit({"job_id": job_dir.name, "lines": text.splitlines()})
    print(text or "(no logs yet)")
    return 0


def run_cancel(ctx, args) -> int:
    job_dir = _resolve(ctx, args.ref)
    if ctx.dry_run():
        return ctx.emit({"job_id": job_dir.name, "dry_run": True, "action": "cancel"},
                        dry_run=True,
                        human=lambda: ctx.ui.info(f"[dry-run] would cancel job {job_dir.name}"))
    killed = jobs.cancel(job_dir)
    data = {"job_id": job_dir.name, "cancelled": killed, "process_killed": killed}
    return ctx.emit(data, human=lambda: ctx.ui.ok(f"Cancelled {job_dir.name}") if killed else
                    ctx.ui.warn(f"Job {job_dir.name} was not cancelled; it has finished or its worker could not be verified"))
