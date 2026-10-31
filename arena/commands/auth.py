"""arena login / arena check —— 登录与登录态体检。"""

from __future__ import annotations

from ..engine import cmd_check, cmd_login
from ..session import open_session


def run_login(ctx, args) -> int:
    if ctx.dry_run():
        return ctx.emit({"action": "login", "profile": ctx.paths.profile,
                         "email": args.email, "wait": args.wait,
                         "state_file": str(ctx.paths.state_file)},
                        dry_run=True,
                        human=lambda: ctx.ui.kv([
                            ("操作", "login（不会真的执行）"),
                            ("profile", ctx.paths.profile),
                            ("邮箱", args.email or "（未提供，需在窗口里手动完成）"),
                            ("等待", f"{args.wait}s"),
                        ], title="[dry-run] arena login"))

    ctx.ui.debug(f"login profile={ctx.paths.profile}")
    if not args.email:
        ctx.ui.info("没给 --email：会打开登录弹窗，由你在窗口里完成登录")
    with open_session(ctx) as session:
        res = cmd_login(session, args.email, args.code, args.wait, args.probe_text)

    state = "已是最新登录态" if res.get("already_logged_in") else "登录成功"
    data = {**res, "profile": ctx.paths.profile}
    return ctx.emit(data, human=lambda: (
        ctx.ui.ok(f"{state}，会话已保存：{res.get('state_file')}"),
        ctx.ui.kv([("profile", ctx.paths.profile), ("cookies", res.get("cookies")),
                   ("state_file", res.get("state_file"))]),
        ctx.ui.info('下一步：arena ask "你的问题"'),
    ))


def run_check(ctx, args) -> int:
    with open_session(ctx) as session:
        res = cmd_check(session, probe=args.probe, probe_text=args.probe_text)

    logged_in = res.get("logged_in")
    label = {True: "已登录", False: "未登录", None: "无法确认"}[logged_in]

    def human() -> None:
        (ctx.ui.ok if logged_in else ctx.ui.warn)(f"{label} · profile「{ctx.paths.profile}」")
        ctx.ui.kv([("state_file", res.get("state_file")), ("cookies", res.get("cookies")),
                   ("登录凭证", res.get("auth_cookie_names")), ("页面", res.get("url"))])
        if res.get("hint"):
            ctx.ui.info(res["hint"])

    ctx.emit({**res, "profile": ctx.paths.profile}, human=human)
    # 便于脚本判断：确定未登录 -> 2
    return 2 if logged_in is False else 0
