"""arena jobs —— 后台任务（由 `arena ask --no-wait` 产生）。"""

from __future__ import annotations

from .. import jobs


def _resolve(ctx, ref: str):
    return jobs.resolve(ctx.paths.home, ref)


def run_list(ctx, args) -> int:
    items = jobs.list_jobs(ctx.paths.home, limit=args.limit)
    data = {"jobs": items, "count": len(items)}

    def human() -> None:
        ctx.ui.table(["job_id", "状态", "耗时", "prompt"],
                     [[j["job_id"], j["status"],
                       f"{j['elapsed_seconds']}s" if j.get("elapsed_seconds") else "-",
                       (j["prompt"] or "")[:52]] for j in items],
                     title=f"后台任务（profile「{ctx.paths.profile}」）")
        if items:
            ctx.ui.info("arena jobs result <job_id|last> 取结果；arena jobs logs <id> 看日志")

    return ctx.emit(data, human=human)


def run_status(ctx, args) -> int:
    item = jobs.status_of(_resolve(ctx, args.ref))
    data = item

    def human() -> None:
        ctx.ui.kv([
            ("job_id", item["job_id"]),
            ("状态", item["status"]),
            ("创建于", item["created_at"]),
            ("耗时", f"{item['elapsed_seconds']}s" if item.get("elapsed_seconds") else "-"),
            ("profile", item.get("profile")),
            ("prompt", (item.get("prompt") or "")[:80]),
            ("会话链接", item.get("url")),
        ], title="任务状态")
        if item["status"] == "running":
            ctx.ui.info("还在跑：稍后用 arena jobs result 取结果，或 arena jobs logs 看进度")
        elif item["status"] == "failed":
            ctx.ui.warn(f"失败：{(item.get('error') or {}).get('message', '')}")

    return ctx.emit(data, human=human)


def run_result(ctx, args) -> int:
    item = jobs.status_of(_resolve(ctx, args.ref))
    status = item["status"]

    if status == "running":
        data = {"job_id": item["job_id"], "status": status, "ready": False,
                "hint": "任务还在跑；稍后再试，或 arena jobs logs"}
        def human_running() -> None:
            ctx.ui.warn(f"任务 {item['job_id']} 还在跑（{status}）")
            ctx.ui.info("稍后再取，或 arena jobs logs 看进度")
        return ctx.emit(data, human=human_running)

    data = {"job_id": item["job_id"], "status": status, "ready": True, **item}
    if ctx.ui.json_mode or getattr(ctx.args, "jq", None):
        return ctx.emit(data)

    if status == "done" and item.get("answer"):
        limit = ctx.get("max_chars", 0)
        answer = item["answer"]
        if limit and len(answer) > limit:
            answer = answer[:limit].rstrip() + f"\n\n…（已截断，共 {len(answer)} 字）"
            if item.get("url"):
                answer += f"\n完整内容：{item['url']}"
        print(answer)
        return 0

    err = item.get("error") or {}
    ctx.ui.fail(err.get("code", "FAILED"), err.get("error", "任务失败"),
                "arena jobs logs 看原因")
    return 1


def run_logs(ctx, args) -> int:
    job_dir = _resolve(ctx, args.ref)
    text = jobs.logs(job_dir, tail=args.tail)
    if ctx.ui.json_mode:
        return ctx.emit({"job_id": job_dir.name, "lines": text.splitlines()})
    print(text or "（还没有日志）")
    return 0


def run_cancel(ctx, args) -> int:
    job_dir = _resolve(ctx, args.ref)
    if ctx.dry_run():
        return ctx.emit({"job_id": job_dir.name, "dry_run": True, "action": "cancel"},
                        dry_run=True,
                        human=lambda: ctx.ui.info(f"[dry-run] 会取消任务 {job_dir.name}"))
    killed = jobs.cancel(job_dir)
    data = {"job_id": job_dir.name, "cancelled": True, "process_killed": killed}
    return ctx.emit(data, human=lambda: ctx.ui.ok(
        f"已取消 {job_dir.name}" + ("" if killed else "（进程已自行结束）")))
