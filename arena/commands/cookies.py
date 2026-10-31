"""arena cookies import|export|show —— 会话（cookies）管理。"""

from __future__ import annotations

import time
from pathlib import Path

from ..engine import (cmd_export, cmd_import, parse_cookie_file, summarize_cookies)
from ..errors import confirmation
from ..session import open_session


def _existing_session(ctx) -> dict:
    path = ctx.paths.state_file
    if not path.exists():
        return {"exists": False, "has_auth_cookie": False}
    try:
        cookies, _ = parse_cookie_file(path)
    except Exception:  # noqa: BLE001
        return {"exists": True, "has_auth_cookie": False}
    health = summarize_cookies(cookies, ctx.engine_cfg)
    return {"exists": True, **health}


def run_import(ctx, args) -> int:
    src = Path(args.path).expanduser()
    if not src.exists():
        from ..errors import ArenaError
        raise ArenaError("validation", "not_found", f"文件不存在：{src}",
                         "检查路径，或让用户重新上传 cookies 文件", exit_code=1)

    current = _existing_session(ctx)
    would_overwrite = bool(current.get("has_auth_cookie"))

    # ---- dry-run：只解析文件，不落盘 ----
    if ctx.dry_run():
        cookies, meta = parse_cookie_file(src)
        health = summarize_cookies(cookies, ctx.engine_cfg)
        data = {"source": str(src), "format": meta.get("format"), **health,
                "would_overwrite": would_overwrite,
                "target": str(ctx.paths.state_file)}
        return ctx.emit(data, dry_run=True, human=lambda: (
            ctx.ui.kv([("来源", data["source"]), ("格式", data["format"]),
                       ("条数", data["count"]), ("域名", data["domains"]),
                       ("登录凭证", data["auth_cookie_names"] or "未识别"),
                       ("会覆盖现有登录态", "是" if would_overwrite else "否")],
                      title="[dry-run] arena cookies import"),
            ctx.ui.info("真正导入请去掉 --dry-run（覆盖登录态需要 --yes）"),
        ))

    # 覆盖健康登录态 = high-risk：必须显式确认
    if would_overwrite and not getattr(args, "yes", False) and not getattr(args, "force", False):
        raise confirmation(
            f"覆盖 profile「{ctx.paths.profile}」现有的登录态（{len(current.get('auth_cookie_names') or [])} 个凭证 cookie）",
            "确认后加 --yes；或用 --profile <名字> --cookies <文件> 导入到另一个 profile")

    res = cmd_import(src, ctx.paths.state_file, ctx.engine_cfg)
    data = {**res, "profile": ctx.paths.profile, "overwrote": would_overwrite}

    def human() -> None:
        ctx.ui.ok(f"已导入 {res['count']} 条 cookies → {res['state_file']}")
        ctx.ui.kv([("来源", res["source"]), ("格式", res["format"]), ("域名", res["domains"]),
                   ("登录凭证", res["auth_cookie_names"] or "未识别"),
                   ("已过期", res["expired_names"] or "-"), ("profile", ctx.paths.profile)])
        if res.get("warning"):
            ctx.ui.warn(res["warning"])
        if res["has_auth_cookie"]:
            ctx.ui.info('下一步：arena check，或直接 arena ask "你的问题"')
        else:
            ctx.ui.warn("没找到登录凭证 cookie：这份文件可能不含登录态")

    return ctx.emit(data, human=human)


def run_export(ctx, args) -> int:
    out = Path(args.out).expanduser() if args.out else ctx.paths.home / "cookies.json"
    if ctx.dry_run():
        return ctx.emit({"action": "export", "target": str(out)}, dry_run=True,
                        human=lambda: ctx.ui.info(f"[dry-run] 会把当前会话导出到 {out}"))
    with open_session(ctx) as session:
        res = cmd_export(session, out)
    return ctx.emit(res, human=lambda: (
        ctx.ui.ok(f"已导出 {res['cookies']} 条 cookies → {res['exported']}"),
        ctx.ui.warn("该文件等同于账号密码，别外传、别提交到仓库"),
    ))


def run_show(ctx, args) -> int:
    path = ctx.paths.state_file
    if not path.exists():
        from ..errors import of
        raise of("LOGIN_REQUIRED", f"没有会话文件：{path}")
    cookies, _ = parse_cookie_file(path)
    health = summarize_cookies(cookies, ctx.engine_cfg)
    now = time.time()
    rows = []
    for c in sorted(cookies, key=lambda c: c["name"]):
        exp = c.get("expires", -1)
        if exp in (-1, 0):
            exp_s = "session"
        else:
            exp_s = time.strftime("%Y-%m-%d", time.localtime(exp))
            if exp < now:
                exp_s += " (过期)"
        rows.append([c["name"], c.get("domain", ""), exp_s,
                     "httpOnly" if c.get("httpOnly") else ""])

    data = {"state_file": str(path), "profile": ctx.paths.profile, **health,
            "cookies": [{"name": c["name"], "domain": c.get("domain"),
                         "expires": c.get("expires")} for c in cookies]}

    def human() -> None:
        ctx.ui.kv([("profile", ctx.paths.profile), ("state_file", str(path)),
                   ("cookies", health["count"]),
                   ("登录凭证", health["auth_cookie_names"] or "未识别"),
                   ("已过期", health["expired_names"] or "-")])
        ctx.ui.table(["name", "domain", "expires", "flag"], rows, title="\ncookies（不显示值）")

    ctx.emit(data, human=human)
    return 0 if health["has_auth_cookie"] else 2
