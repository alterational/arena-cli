"""arena CLI —— 命令分发、全局参数、风险分级、结构化输出、退出码。

    arena <命令> [子命令] [参数] [全局 flag]

设计参照 lark-cli：
  * 顶层 help 有 AGENT QUICKSTART，说明 agent 该怎么用
  * 每条命令标注风险等级 read | write | high-risk-write，高危要 --yes
  * --json 输出 {ok, identity, data, meta} 信封；错误是 {ok:false, error:{type,subtype,message,hint}}
  * --jq 过滤 JSON、--dry-run 只预览不执行、--no-wait 挂后台
  * arena schema <命令> 给 agent 自省参数
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Optional

from . import __version__
from .config import SETTABLE, build_context
from .ui import UI

USAGE_ERROR = 64          # BSD sysexits：用法错误，和业务退出码（2/3/4/5/6）区分

# 风险分级（对齐 lark-cli 的 read / write / high-risk-write）
RISK: dict[str, str] = {
    "ask": "write",
    "login": "write",
    "check": "read",
    "status": "read",
    "cookies.import": "write",
    "cookies.export": "write",
    "cookies.show": "read",
    "chat.list": "read",
    "chat.last": "read",
    "chat.open": "read",
    "config.list": "read",
    "config.show": "read",
    "config.get": "read",
    "config.path": "read",
    "config.set": "write",
    "config.unset": "write",
    "profile.list": "read",
    "profile.show": "read",
    "profile.create": "write",
    "profile.use": "write",
    "profile.delete": "high-risk-write",
    "jobs.list": "read",
    "jobs.status": "read",
    "jobs.result": "read",
    "jobs.logs": "read",
    "jobs.cancel": "write",
    "schema": "read",
    "doctor": "read",
    "version": "read",
}

EXAMPLES = """\
示例（按推荐顺序）:
  arena ask "帮我调研三家竞品的定价策略"              # 一条命令完成 提问→等待→取回
  arena ask --no-wait "跑一个长任务"                  # 挂后台，立刻返回 job_id
  arena jobs result last                            # 回来看结果
  arena status                                      # 登录态 + 环境 + 最近任务
  arena schema ask                                  # 看 ask 的参数/风险/默认值
  arena cookies import ~/uploads/arena.ai_cookies.txt --yes
  arena config set timeout 1800
"""

QUICKSTART = """\
arena — 用命令行驱动 arena.ai/agent（Agent Mode）

AGENT QUICKSTART（你是 agent？从这里开始）:
    看有哪些命令:   arena --help
    看某命令细节:   arena schema <命令>          # 参数、类型、默认值、风险等级
    提问拿结果:     arena ask "<用户原话>"        # 退出码 0 = 成功，stdout 就是回答
    长任务别阻塞:   arena ask --no-wait "<…>"  →  arena jobs result last
    结构化输出:     --json（{ok, identity, data, meta}），--jq '.data.answer' 过滤
    只预览不执行:   --dry-run
    风险: 每条命令有 read | write | high-risk-write；high-risk-write 必须加 --yes（先问用户）
    失败处理: 看 error.subtype 与 error.hint（LOGIN_REQUIRED → arena cookies import）
"""


class Parser(argparse.ArgumentParser):
    """argparse 默认用 2 表示用法错误，会和「需要登录=2」撞车，这里改成 64。"""

    def error(self, message: str) -> "None":  # type: ignore[override]
        self.print_usage(sys.stderr)
        sys.stderr.write(f"arena: 错误: {message}\n提示: `arena --help` 或 `arena schema <命令>`\n")
        raise SystemExit(USAGE_ERROR)


def _add_global(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    S = argparse.SUPPRESS
    g = p.add_argument_group("全局参数")
    g.add_argument("--json", action="store_true", default=S, help="输出 {ok, identity, data, meta} 信封")
    g.add_argument("--format", choices=["text", "json"], default=S, help="输出格式")
    g.add_argument("--jq", default=S, help="用简化 jq 表达式过滤 JSON，如 '.data.answer'")
    g.add_argument("-q", "--quiet", action="store_true", default=S, help="只输出结果")
    g.add_argument("-v", "--verbose", action="store_true", default=S, help="显示调试信息（含浏览器日志）")
    g.add_argument("--no-color", action="store_true", default=S, help="关闭颜色")
    g.add_argument("--dry-run", action="store_true", default=S, help="只预览要做什么，不真正执行")
    g.add_argument("--yes", action="store_true", default=S, help="确认执行 high-risk-write 操作")
    g.add_argument("--no-wait", action="store_true", default=S, help="ask：挂后台跑，立刻返回 job_id")
    g.add_argument("--profile", default=S, help="使用哪个 profile（默认 default）")
    g.add_argument("--config", default=S, help="配置文件路径（默认 ~/.arena-agent/config.json）")
    g.add_argument("--base-url", default=S, help="站点根地址（默认 https://arena.ai）")
    g.add_argument("--timeout", type=float, default=S, help="等待 Agent 完成的上限秒数")
    g.add_argument("--stable", type=float, default=S, help="文本稳定多少秒算完成")
    g.add_argument("--max-chars", type=int, default=S, help="长回答截断长度（0 = 不截断）")
    g.add_argument("--cookies", default=S,
                   help="先导入这份 cookies 文件（Netscape .txt / JSON），再执行命令")
    g.add_argument("--headed", action="store_true", default=S, help="显示浏览器窗口")
    g.add_argument("--state-file", default=S, help=argparse.SUPPRESS)
    g.add_argument("--profile-dir", default=S, help=argparse.SUPPRESS)
    return p


def _sub(sub, path: str, help_text: str, description: str = "",
         common: Optional[argparse.ArgumentParser] = None) -> argparse.ArgumentParser:
    """path 用点分表示层级（如 cookies.import），但注册的命令名只取最后一段。"""
    name = path.split(".")[-1]
    risk = RISK.get(path)                      # 只有登记过的叶子命令才标风险
    epilog = (f"Risk: {risk}" + ("（需要 --yes 确认）" if risk == "high-risk-write" else "")
              ) if risk else ""
    p = sub.add_parser(name, parents=[common], help=help_text,
                       description=description or help_text,
                       epilog=epilog,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    return p


def build_parser() -> Parser:
    common = argparse.ArgumentParser(add_help=False)
    _add_global(common)

    p = Parser(
        prog="arena",
        description=QUICKSTART,
        epilog=EXAMPLES + "\n退出码: 0 成功 · 1 一般错误 · 2 需要登录 · 3 发送失败 · 4 超时 · "
                          "5 页面改版 · 6 人机校验 · 64 用法错误\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_global(p)
    p.add_argument("--version", action="store_true", default=argparse.SUPPRESS, help="显示版本")

    sub = p.add_subparsers(dest="cmd", metavar="<命令>", parser_class=Parser)

    # ---- ask ----
    a = _sub(sub, "ask", "把内容发给 Agent 并取回结果",
             "默认输出回答正文；--json 输出结构化结果；--no-wait 挂后台。", common)
    a.add_argument("prompt", nargs="*", help="要发送的内容；`-` 或省略表示从 stdin 读")
    a.add_argument("--file", help="从文件读取 prompt")
    a.add_argument("--chat",
                   help="在已有会话里继续提问（chat_id / URL / last / arena chat list 的序号）")
    a.add_argument("--stream", action="store_true", default=argparse.SUPPRESS,
                   help="把生成过程中的增量实时打到 stderr（stdout 仍只有最终回答）")

    # ---- login / check / status ----
    lg = _sub(sub, "login", "登录并保存 cookies",
              "首次进入会先点同意条款，再点发送触发登录弹窗。", common)
    lg.add_argument("--email", help="接收登录链接/验证码的邮箱")
    lg.add_argument("--code", help="一次性验证码（页面要求时）")
    lg.add_argument("--wait", type=float, default=240, help="等待人工完成登录的秒数")
    lg.add_argument("--probe-text", default="hi", help="用于触发登录弹窗的占位消息")

    ck = _sub(sub, "check", "检查登录态是否可用", "", common)
    ck.add_argument("--probe", action="store_true", help="真发一条探测消息来确认（会留聊天记录）")
    ck.add_argument("--probe-text", default="hi")

    _sub(sub, "status", "登录态 + 环境 + 最近任务的综合状态", "", common)

    # ---- cookies ----
    ck2 = _sub(sub, "cookies", "导入/导出/查看 cookies", "", common)
    csub = ck2.add_subparsers(dest="sub", metavar="<子命令>", parser_class=Parser)
    ci = _sub(csub, "cookies.import", "导入 cookies 文件（Netscape .txt / JSON）",
              "会覆盖当前 profile 的登录态，属高危操作，需要 --yes。", common)
    ci.add_argument("path")
    ci.add_argument("--force", action="store_true", help="（兼容旧脚本）等同 --yes")
    ce = _sub(csub, "cookies.export", "导出当前会话 cookies", "", common)
    ce.add_argument("--out", help="输出路径（默认 ~/.arena-agent/cookies.json）")
    _sub(csub, "cookies.show", "查看 cookie 清单（只显示名字，不显示值）", "", common)

    # ---- chat ----
    ch = _sub(sub, "chat", "会话历史", "", common)
    chsub = ch.add_subparsers(dest="sub", metavar="<子命令>", parser_class=Parser)
    cl = _sub(chsub, "chat.list", "列出最近会话", "", common)
    cl.add_argument("--limit", type=int, default=20)
    clast = _sub(chsub, "chat.last", "打印最近一条会话的链接", "", common)
    clast.add_argument("--limit", type=int, default=5)
    co = _sub(chsub, "chat.open", "把会话链接打印出来（可在浏览器打开）", "", common)
    co.add_argument("ref", help="chat_id / 序号 / last / 完整 URL")
    co.add_argument("--browser", action="store_true", help="同时用系统浏览器打开")

    # ---- config ----
    cf = _sub(sub, "config", "读写配置", "", common)
    cfsub = cf.add_subparsers(dest="sub", metavar="<子命令>", parser_class=Parser)
    _sub(cfsub, "config.list", "列出全部配置", "", common)
    _sub(cfsub, "config.show", "同上（别名）", "", common)
    cg = _sub(cfsub, "config.get", "读一个配置项", "", common)
    cg.add_argument("key")
    cs = _sub(cfsub, "config.set", "写一个配置项", "", common)
    cs.add_argument("key", help="可用：" + ", ".join(sorted(SETTABLE)))
    cs.add_argument("value", help="值（数字/true/false/JSON 会自动识别）")
    cu = _sub(cfsub, "config.unset", "删除配置项（回到默认值）", "", common)
    cu.add_argument("key")
    _sub(cfsub, "config.path", "打印配置文件路径", "", common)

    # ---- profile ----
    pf = _sub(sub, "profile", "多套登录态切换", "", common)
    pfsub = pf.add_subparsers(dest="sub", metavar="<子命令>", parser_class=Parser)
    _sub(pfsub, "profile.list", "列出所有 profile", "", common)
    _sub(pfsub, "profile.show", "当前 profile 详情", "", common)
    pc = _sub(pfsub, "profile.create", "新建 profile", "", common)
    pc.add_argument("name")
    pc.add_argument("--from", dest="from_cookies", help="顺便从这个 cookies 文件导入会话")
    pu = _sub(pfsub, "profile.use", "切换默认 profile", "", common)
    pu.add_argument("name")
    pd = _sub(pfsub, "profile.delete", "删除 profile（高危，需要 --yes）", "", common)
    pd.add_argument("name")

    # ---- jobs ----
    jb = _sub(sub, "jobs", "后台任务（ask --no-wait 产生的任务）", "", common)
    jsub = jb.add_subparsers(dest="sub", metavar="<子命令>", parser_class=Parser)
    jl = _sub(jsub, "jobs.list", "列出后台任务", "", common)
    jl.add_argument("--limit", type=int, default=20)
    for verb, text in (("status", "任务状态"), ("result", "取回任务结果"),
                       ("logs", "看任务日志"), ("cancel", "取消任务")):
        jp = _sub(jsub, f"jobs.{verb}", text, "", common)
        jp.add_argument("ref", nargs="?", default="last", help="job id / 前缀 / last（默认 last）")
        if verb == "logs":
            jp.add_argument("--tail", type=int, default=40)

    # ---- schema / doctor / version ----
    sc = _sub(sub, "schema", "查看某命令的参数/风险/默认值（agent 自省）", "", common)
    sc.add_argument("path", nargs="*", help="命令路径，如 ask / cookies import / jobs result")
    _sub(sub, "doctor", "环境体检（依赖/浏览器/网络/会话/技能安装）", "", common)
    _sub(sub, "version", "版本信息", "", common)
    _sub(sub, "help", "显示帮助", "", common)
    return p


# --------------------------------------------------------------------------- 分发
def _dispatch(args, ctx) -> int:
    from .commands import admin, ask as ask_cmd, auth, chat, config_cmd, cookies, jobs_cmd, profile, schema_cmd

    key = (args.cmd, getattr(args, "sub", None))
    table: dict[tuple[str, Optional[str]], Callable[[Any, Any], int]] = {
        ("ask", None): ask_cmd.run,
        ("login", None): auth.run_login,
        ("check", None): auth.run_check,
        ("status", None): admin.run_status,
        ("cookies", "import"): cookies.run_import,
        ("cookies", "export"): cookies.run_export,
        ("cookies", "show"): cookies.run_show,
        ("chat", "list"): chat.run_list,
        ("chat", "last"): chat.run_last,
        ("chat", "open"): chat.run_open,
        ("config", "list"): config_cmd.run_list,
        ("config", "show"): config_cmd.run_list,
        ("config", "get"): config_cmd.run_get,
        ("config", "set"): config_cmd.run_set,
        ("config", "unset"): config_cmd.run_unset,
        ("config", "path"): config_cmd.run_path,
        ("profile", "list"): profile.run_list,
        ("profile", "show"): profile.run_show,
        ("profile", "create"): profile.run_create,
        ("profile", "use"): profile.run_use,
        ("profile", "delete"): profile.run_delete,
        ("jobs", "list"): jobs_cmd.run_list,
        ("jobs", "status"): jobs_cmd.run_status,
        ("jobs", "result"): jobs_cmd.run_result,
        ("jobs", "logs"): jobs_cmd.run_logs,
        ("jobs", "cancel"): jobs_cmd.run_cancel,
        ("schema", None): schema_cmd.run,
        ("doctor", None): admin.run_doctor,
        ("version", None): admin.run_version,
    }
    handler = table.get(key)
    if handler is None:
        from .errors import unknown_command
        raise unknown_command(
            f"{args.cmd} {getattr(args, 'sub', '') or ''}".strip(),
            sorted({k[0] for k in table} | {"schema", "jobs", "status"}))
    return handler(ctx, args)


def _risk_of(args) -> str:
    key = f"{args.cmd}.{getattr(args, 'sub', '')}" if getattr(args, "sub", None) else args.cmd
    return RISK.get(key, "read")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "version", False):
        print(f"arena {__version__}")
        return 0
    if not getattr(args, "cmd", None) or args.cmd == "help":
        parser.print_help()
        return 0

    fmt = getattr(args, "format", None)
    json_mode = bool(getattr(args, "json", False) or fmt == "json")
    ui = UI(
        json_mode=json_mode,
        color=False if getattr(args, "no_color", False) else None,
        quiet=bool(getattr(args, "quiet", False)),
        verbose=bool(getattr(args, "verbose", False)),
    )
    ctx = build_context(args, ui)

    from .errors import ArenaError, confirmation, from_skill_error
    from .engine import SkillError, cmd_import
    from pathlib import Path

    def fail(err: ArenaError) -> int:
        if ui.json_mode or getattr(args, "jq", None):
            print(json.dumps({"ok": False, "identity": ctx.paths.profile,
                              "error": err.to_dict()}, ensure_ascii=False, indent=2))
        else:
            ui.err(f"{err.type}.{err.subtype}: {err.message}")
            if err.hint:
                print(f"  {ui.paint('→ ' + err.hint, 'grey')}", file=sys.stderr)
        return err.exit_code

    try:
        # 全局 --cookies：先导入（高危，需要 --yes）
        cookies_file = getattr(args, "cookies", None)
        if cookies_file:
            target = Path(cookies_file).expanduser()
            if not getattr(args, "yes", False) and not getattr(args, "dry_run", False):
                raise confirmation(f"用 {target.name} 覆盖 profile「{ctx.paths.profile}」的登录态",
                                   "确认后加 --yes；或 --dry-run 先看会导入什么")
            imp = cmd_import(target, ctx.paths.state_file, ctx.engine_cfg)
            ui.debug(f"导入 {imp['count']} 条 cookies（登录凭证: "
                     f"{', '.join(imp['auth_cookie_names']) or '未识别'}）")
            if not imp["has_auth_cookie"]:
                ui.warn("这份 cookies 里没识别出登录凭证，可能仍需 arena login")

        # 风险闸门
        risk = _risk_of(args)
        needs_confirm = (risk == "high-risk-write"
                         and not getattr(args, "yes", False)
                         and not getattr(args, "dry_run", False))
        if needs_confirm:
            raise confirmation(f"arena {args.cmd} {getattr(args, 'sub', '') or ''}".strip())

        return _dispatch(args, ctx)
    except ArenaError as err:
        return fail(err)
    except SkillError as exc:
        return fail(from_skill_error(exc))
    except SystemExit:
        raise
    except FileNotFoundError as exc:
        return fail(ArenaError("validation", "not_found", str(exc), "arena jobs list"))
    except KeyboardInterrupt:
        ui.err("已中断")
        return 130
    except BrokenPipeError:
        return 0
    except Exception as exc:  # noqa: BLE001
        from playwright.sync_api import Error as PWError
        if isinstance(exc, PWError):
            return fail(ArenaError("runtime", "browser_error", str(exc).splitlines()[0],
                                   "用 --headed 观察页面，或 arena doctor"))
        if ui.verbose:
            import traceback
            traceback.print_exc()
        return fail(ArenaError("runtime", "internal", f"{type(exc).__name__}: {exc}",
                               "加 -v 看完整堆栈；或 arena doctor"))


if __name__ == "__main__":
    sys.exit(main())
