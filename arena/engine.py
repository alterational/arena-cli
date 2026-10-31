#!/usr/bin/env python3
"""
arena_agent.py — 用「真实浏览器」驱动 https://arena.ai/agent

能力：
  1. 打开 arena.ai/agent（Playwright 驱动的 Chromium，browser-use 底层同款）
  2. 登录（邮箱 magic-link / 一次性验证码），并把 cookies + localStorage 持久化
  3. 把用户输入的内容写进页面的 Agent 输入框（Tiptap / ProseMirror）
  4. 提交、等待 Agent Mode 执行完成（自动识别流式结束）
  5. 取回回答，输出成 JSON / 纯文本 / Markdown，交给上层（Hermes）转发给用户

命令：
  login            交互式登录并保存会话（cookies）
  ask "问题"       发送一条消息，等待结果，打印回答
  check            检查当前保存的会话是否仍然有效
  export-cookies   把当前会话导出成 cookies JSON
  import-cookies   从浏览器导出的 cookies JSON 导入会话

常用参数：
  --base-url https://arena.ai      目标站点（自测时可指向 mock 服务）
  --profile-dir ~/.arena-agent/prof   浏览器用户目录（持久化，最稳的"记住登录"）
  --state-file  ~/.arena-agent/state.json  会话快照（cookies + localStorage，可移植）
  --headed                         显示浏览器窗口（调试 / 手动过验证码时用）
  --json / --text / --markdown     输出格式

退出码：
  0 成功
  2 需要登录（LOGIN_REQUIRED）
  3 发送失败（SEND_FAILED）
  4 等待超时（TIMEOUT）
  5 页面状态异常（UI_CHANGED，选择器需要更新）
  6 被人机校验拦住（BLOCKED，需要 --headed 人工/导入 cookies）
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

PLAYWRIGHT_AVAILABLE = True
try:
    from playwright.sync_api import sync_playwright, Page, BrowserContext, Locator
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import TimeoutError as PWTimeout
except ImportError:  # 没装 playwright 时，CLI 仍要能跑 doctor/config/cookies 等离线命令
    PLAYWRIGHT_AVAILABLE = False
    sync_playwright = None  # type: ignore[assignment]
    Page = BrowserContext = Locator = object  # type: ignore[assignment,misc]

    class PWError(Exception):  # type: ignore[no-redef]
        pass

    class PWTimeout(PWError):  # type: ignore[no-redef]
        pass

HOME = Path.home()
DEFAULT_DIR = HOME / ".arena-agent"

# --------------------------------------------------------------------------------------
# 默认配置：所有选择器都可以在 config.json 里覆盖，页面改版时只改配置、不改代码
# --------------------------------------------------------------------------------------
DEFAULT_CONFIG: dict[str, Any] = {
    "base_url": "https://arena.ai",
    "agent_path": "/agent",
    # 输入框（composer）。arena.ai 现在是 Tiptap/ProseMirror 编辑器
    "editor_selectors": [
        'div.tiptap.ProseMirror[contenteditable="true"]',
        '.ProseMirror[contenteditable="true"]',
        'div[contenteditable="true"][role="textbox"]',
        'textarea[placeholder]',
    ],
    # 发送按钮
    "send_selectors": [
        'button[aria-label="Send message"]',
        'button[aria-label*="Send" i]',
        'button[type="submit"]',
    ],
    # 流式输出中 / 可停止的迹象（出现即视为还没生成完）
    "streaming_selectors": [
        'button[aria-label*="Stop" i]',
        '[data-streaming="true"]',
        '[data-state="streaming"]',
    ],
    # 对话回合（每轮用户/助手各一个）
    "turn_selectors": ["[data-agent-transcript-message]"],
    # 回答正文的容器（按顺序尝试，命中最后一个可见的）
    # 实测 arena.ai：助手回合 = [data-agent-transcript-message] 且不含 [data-user-message-body-row]
    #              正文 = 该回合里的 .prose（时间戳/反馈按钮在 .prose 之外，天然被排除）
    "assistant_selectors": [
        '[data-agent-transcript-message]:not(:has([data-user-message-body-row])) .prose',
        '[data-agent-transcript-message]:not(:has([data-user-message-body-row]))',
        '[data-message-author-role="assistant"]',
        '[data-role="assistant"]',
        '[data-testid*="assistant"]',
        'div.prose:not(.tiptap):not(.ProseMirror)',
        '[class*="assistant"] .prose',
        '.message-assistant',
        'article .prose',
    ],
    # 用户消息容器（用于判断"消息是否真的发出去了"）
    "user_selectors": [
        '[data-user-message-body-row]',
        '[data-message-author-role="user"]',
        '[data-role="user"]',
        '[data-testid*="user"]',
        '.message-user',
    ],
    # 抽到的文本里，若出现这些"页面 UI 文案"就截断（兜底策略用）
    "answer_stop_markers": [
        "Was this task successful?",
        "Keep working",
        "All files that are created or uploaded in this session",
        "Stop response",
    ],
    # 会话结束后 Arena 会把输入框替换成「Was this task successful?」反馈卡（Esc / × 可关），
    # 关掉之后 composer 才会回来 —— 多轮追问必须先把这张卡关掉
    "feedback_text": "Was this task successful?",
    "feedback_dismiss_selectors": [
        'button[aria-label*="Close" i]',
        'button[aria-label*="Dismiss" i]',
        'button:has-text("Esc")',
    ],
    # 弹窗文案特征
    "terms_dialog_text": "Terms of Use",
    "terms_agree_texts": ["Agree", "I Agree", "同意"],
    "login_dialog_text": "Log In or Create Account",
    "login_trigger_texts": ["Continue with Google", "Continue with email", "Log in", "Sign in"],
    "email_input_selectors": [
        '[role="dialog"] input[type="email"]',
        'input[type="email"]',
    ],
    "continue_email_texts": ["Continue with email", "Continue", "Next", "Send code"],
    "code_input_selectors": [
        '[role="dialog"] input[autocomplete="one-time-code"]',
        '[role="dialog"] input[inputmode="numeric"]',
        '[role="dialog"] input[maxlength="1"]',
        '[role="dialog"] input[maxlength="6"]',
        '[role="dialog"] input[type="text"]:not([type="email"])',
    ],
    # 回答区里要剔除的 UI 噪音（按钮文案等）
    "noise_lines": [
        "Copy", "Copied", "Retry", "Regenerate", "Edit", "Share", "Like", "Dislike",
        "Sources", "Thought for", "Show more", "Show less", "Stop",
    ],
    # 用来判断"这份 cookies 里到底有没有登录态"的 cookie 名特征
    "auth_cookie_patterns": ["arena-auth", "__session", "next-auth", "session", "auth", "token"],
    # 只有登录后才会出现的账号入口（用于 check 判断）
    # 未登录落地页的特征文案 / 登录后才会有的侧边栏入口
    "anonymous_markers": ["Experience the frontier"],
    "logged_in_selectors": [
        'a[href="/history/search"]',
        'button[aria-label*="New chat" i]',
        '[aria-label*="New Chat" i]',
    ],
    "account_selectors": [
        'button[aria-label*="account" i]',
        '[aria-label*="profile" i]',
        '[data-testid*="avatar" i]',
        'button[aria-label*="settings" i]',
    ],
    # Cloudflare 等"人机校验"中间页的特征文案
    "challenge_markers": [
        "Performing security verification",
        "Verify you are human",
        "Just a moment",
        "Checking your browser",
        "needs to review the security of your connection",
    ],
    "challenge_iframe_selectors": [
        'iframe[src*="challenges.cloudflare.com"]',
        'iframe[title*="challenge" i]',
        '#cf-challenge-running',
    ],
    "error_markers": [
        "Something went wrong",
        "An error occurred",
        "rate limit",
        "Too many requests",
        "You have reached",
        "Please log in",
    ],
    # 行为参数
    "wait_timeout": 900,        # 等待回答的总超时（秒）
    "stable_seconds": 2.5,      # 文本连续多少秒不再变化即认为生成结束
    "poll_seconds": 1.0,
    "login_timeout": 240,       # login 命令等待人工完成登录的秒数
    "viewport": {"width": 1440, "height": 900},
    "locale": "en-US",
}


def load_config(explicit: Optional[str], overrides: dict[str, Any]) -> dict[str, Any]:
    """默认配置 <- /home/user/.arena-agent/config.json <- --config 文件 <- 命令行覆盖"""
    cfg = dict(DEFAULT_CONFIG)
    for path in [DEFAULT_DIR / "config.json", Path(explicit) if explicit else None]:
        if path and Path(path).exists():
            cfg.update(json.loads(Path(path).read_text(encoding="utf-8")))
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg


# --------------------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------------------
def log(msg: str) -> None:
    sys.stderr.write(f"[arena] {msg}\n")
    sys.stderr.flush()


def jitter(a: float = 0.05, b: float = 0.2) -> None:
    time.sleep(random.uniform(a, b))


def first_visible(page: Page, selectors: Iterable[str]) -> Optional[Locator]:
    for sel in selectors:
        try:
            loc = page.locator(sel).last
            if loc.count() and loc.is_visible():
                return loc
        except PWError:
            continue
    return None


def visible_of(page: Page, selectors: Iterable[str]) -> list[Locator]:
    out: list[Locator] = []
    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(loc.count()):
                item = loc.nth(i)
                try:
                    if item.is_visible():
                        out.append(item)
                except PWError:
                    pass
        except PWError:
            continue
    return out


def click_text(page: Page, texts: Iterable[str], timeout_ms: int = 4000) -> Optional[str]:
    """按可访问名/文案点击按钮或链接，返回命中的文案。"""
    for text in texts:
        pattern = re.compile(re.escape(text), re.I)
        for role in ("button", "link"):
            try:
                loc = page.get_by_role(role, name=pattern).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=timeout_ms)
                    return text
            except (PWError, PWTimeout, StopIteration):
                continue
        try:  # 兜底：任意带这段文字的可见元素
            loc = page.locator(f'text={text}').first
            if loc.count() and loc.is_visible():
                loc.click(timeout=timeout_ms)
                return text
        except (PWError, PWTimeout):
            continue
    return None


def body_text(page: Page) -> str:
    try:
        return page.inner_text("body")
    except PWError:
        return ""


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


# --------------------------------------------------------------------------------------
# 浏览器会话：profile 目录（持久） + state.json（可移植快照）
# --------------------------------------------------------------------------------------
@dataclass
class Session:
    cfg: dict[str, Any]
    profile_dir: Path
    state_file: Path
    headed: bool = False
    keep_alive: bool = False
    channel: Optional[str] = None
    user_agent: Optional[str] = None
    _challenge_logged_at: float = field(default=0.0, repr=False)
    _pw: Any = field(default=None, repr=False)
    ctx: Optional[BrowserContext] = field(default=None, repr=False)
    page: Optional[Page] = field(default=None, repr=False)

    # ---- 生命周期 -------------------------------------------------------------
    def __enter__(self) -> "Session":
        if not PLAYWRIGHT_AVAILABLE:
            raise SkillError(
                "DEPENDENCY_MISSING",
                "缺少 playwright：pip install playwright && python3 -m playwright install chromium",
            )
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        self.ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=not self.headed,
            viewport=self.cfg["viewport"],
            locale=self.cfg["locale"],
            channel=self.channel,          # 例如 "chrome"：用本机真实 Chrome，过校验成功率更高
            user_agent=self.user_agent,
            args=launch_args,
            ignore_default_args=["--enable-automation"],
        )
        self.ctx.set_default_timeout(15_000)
        self._restore_state()
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self.ctx:
                self.save_state()
        finally:
            try:
                if self.ctx:
                    self.ctx.close()
            finally:
                if self._pw:
                    self._pw.stop()

    # ---- cookies / localStorage 持久化 ---------------------------------------
    def _restore_state(self) -> None:
        """把 state.json 里的 cookies + localStorage 灌进新的 browser context。"""
        if not self.state_file.exists():
            return
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"state 文件损坏，忽略：{self.state_file}")
            return

        cookies = state.get("cookies") or []
        if not cookies and isinstance(state, list):  # 兼容 EditThisCookie 导出格式
            cookies = state
        if cookies:
            try:
                self.ctx.add_cookies(cookies)  # type: ignore[union-attr]
                log(f"已注入 {len(cookies)} 条 cookies")
            except PWError as exc:
                log(f"注入 cookies 失败：{exc}")

        origins = state.get("origins") or []
        if origins:
            blob = {o["origin"]: {kv["name"]: kv["value"] for kv in o.get("localStorage", [])}
                    for o in origins}
            js = "(() => { const S = %s; const o = S[location.origin]; if (o) { for (const k in o) "
            js += "{ try { localStorage.setItem(k, o[k]) } catch (e) {} } } })();"
            self.ctx.add_init_script(script=js % json.dumps(blob))  # type: ignore[union-attr]
            log(f"已注入 {len(origins)} 个 origin 的 localStorage")

    def save_state(self) -> None:
        """把当前 cookies（+ localStorage）写回 state.json —— 也就是"记住 cookies"。"""
        if not self.ctx:
            return
        try:
            state = self.ctx.storage_state()
        except PWError as exc:
            log(f"保存会话失败：{exc}")
            return
        self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(self.state_file, 0o600)  # 会话文件等同密码，收紧权限
        except OSError:
            pass
        log(f"会话已保存：{self.state_file}（{len(state.get('cookies', []))} 条 cookies）")

    # ---- 页面导航 ------------------------------------------------------------
    def open_agent(self) -> Page:
        assert self.page is not None
        url = self.cfg["base_url"].rstrip("/") + self.cfg["agent_path"]
        log(f"打开 {url}")
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except PWTimeout:
            log("页面加载超时，继续尝试（SPA 可能已可用）")
        self.wait_editor()
        self.handle_terms()
        self.settle()
        return self.page

    def settle(self, timeout_ms: int = 20_000) -> None:
        """等 SPA 真正 hydrate 完。

        踩过的坑：`domcontentloaded` 之后编辑器已经在 DOM 里，但 React 还没挂上事件处理器，
        这时点发送是「点了个寂寞」——消息不提交、输入框还留着字，最后抽到一堆页面 UI 文案。
        """
        assert self.page is not None
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except (PWTimeout, PWError):
            pass
        try:
            self.page.wait_for_timeout(400)
        except PWError:
            pass

    def challenge_marker(self) -> Optional[str]:
        """返回命中的"人机校验"特征文案（没有则 None）。"""
        assert self.page is not None
        txt = body_text(self.page)
        for marker in self.cfg["challenge_markers"]:
            if marker.lower() in txt.lower():
                return marker
        return None

    def try_pass_challenge(self) -> bool:
        """尽力而为地帮人机校验过关：点一下 Turnstile 复选框，然后等它自己跳走。

        说明：Cloudflare 托管挑战通常是"无感"的（浏览器指纹合格就自动通过），
        自动点击只是兜底；真被判定为机器人时，正确做法是 --headed 人工过一次，
        或直接把真人浏览器的 cookies 导入（见 import-cookies）。
        """
        marker = self.challenge_marker()
        if not marker:
            return False
        if time.time() - self._challenge_logged_at > 15:
            self._challenge_logged_at = time.time()
            log(f"检测到人机校验页面（{marker}），尝试通过…")
        assert self.page is not None
        for sel in self.cfg["challenge_iframe_selectors"]:
            try:
                frame = self.page.locator(sel).first
                if not frame.count():
                    continue
                box = frame.bounding_box()
                if box:
                    self.page.mouse.click(box["x"] + box["width"] / 2,
                                          box["y"] + box["height"] / 2)
                    log("已点击校验框")
            except PWError:
                continue
        self.page.wait_for_timeout(3000)
        return True

    def open_chat(self, chat: str) -> Page:
        """打开指定会话（支持完整 URL、/agent/<id> 或裸 id）。"""
        assert self.page is not None
        chat = chat.strip()
        if chat.startswith("http"):
            url = chat
        elif chat.startswith("/agent/"):
            url = self.cfg["base_url"].rstrip("/") + chat
        elif chat in ("", "new"):
            return self.open_agent()
        else:
            url = f"{self.cfg['base_url'].rstrip('/')}{self.cfg['agent_path']}/{chat}"
        log(f"打开会话 {url}")
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except PWTimeout:
            log("页面加载超时，继续尝试")
        self.wait_editor()
        self.handle_terms()
        return self.page

    def wait_editor(self, timeout_s: float = 60) -> Locator:
        """等输入框出现；期间会自动处理条款弹窗和人机校验页。"""
        assert self.page is not None
        started = time.time()
        deadline = started + timeout_s
        saw_challenge = False
        while time.time() < deadline:
            loc = first_visible(self.page, self.cfg["editor_selectors"])
            if loc is not None:
                return loc
            if self.dismiss_feedback():       # 会话页：反馈卡挡住了输入框
                continue
            if self.try_pass_challenge():
                saw_challenge = True
                continue
            self.page.wait_for_timeout(700)
        if saw_challenge:
            raise SkillError(
                "BLOCKED",
                "被人机校验（Cloudflare）拦住了。建议：1) 用 --headed 打开窗口手动过校验后重跑；"
                "2) 或从你日常用的浏览器导出 cookies，用 import-cookies 导入；"
                "3) 换网络/降低调用频率再试",
            )
        raise SkillError(
            "UI_CHANGED",
            f"{timeout_s:.0f}s 内没找到输入框（composer）。可能页面改版、被拦截、需要登录，"
            "或「Was this task successful?」反馈卡没关掉；"
            "可用 --headed 观察，并检查 config.json 里的 editor_selectors",
        )

    # ---- 弹窗处理 ------------------------------------------------------------
    def dismiss_feedback(self) -> bool:
        """关掉「Was this task successful?」反馈卡（它占着输入框的位置）。"""
        assert self.page is not None
        page = self.page
        try:
            if self.cfg.get("feedback_text") and self.cfg["feedback_text"] not in body_text(page):
                return False
        except PWError:
            return False
        for sel in self.cfg.get("feedback_dismiss_selectors") or []:
            try:
                btn = page.locator(sel).first
                if btn.count() and btn.is_visible():
                    btn.click(timeout=1500)
                    log("已关闭反馈卡（点关闭按钮）")
                    page.wait_for_timeout(300)
                    return True
            except PWError:
                continue
        try:                                  # 卡片上写着 Esc，直接按键最稳
            page.click("body", position={"x": 5, "y": 5}, timeout=1000)
            page.keyboard.press("Escape")
            log("已关闭反馈卡（Esc）")
            page.wait_for_timeout(300)
            return True
        except PWError:
            return False

    def handle_terms(self, quiet: bool = True) -> bool:
        """首次进入会弹 Terms of Use，需要点 Agree。"""
        assert self.page is not None
        page = self.page
        dialog = page.locator(f'[role="dialog"]:has-text("{self.cfg["terms_dialog_text"]}")')
        try:
            if not dialog.count():
                return False
        except PWError:
            return False
        hit = click_text(page, self.cfg["terms_agree_texts"])
        if hit:
            if not quiet:
                log(f"已同意条款（{hit}）")
            page.wait_for_timeout(800)
            return True
        return False

    def login_dialog_visible(self) -> bool:
        assert self.page is not None
        page = self.page
        try:
            dlg = page.locator(f'[role="dialog"]:has-text("{self.cfg["login_dialog_text"]}")')
            for i in range(dlg.count()):
                if dlg.nth(i).is_visible():   # 只认真正显示出来的弹窗
                    return True
        except PWError:
            pass
        txt = body_text(page)
        return any(t in txt for t in ("Continue with Google", "Continue with email"))

    def list_chats(self, limit: int = 20) -> list[dict[str, Any]]:
        """从侧边栏读最近会话（标题 + 链接）。"""
        assert self.page is not None
        page = self.page
        self.open_agent()
        page.wait_for_timeout(1500)
        try:
            raw = page.evaluate(
                """(limit) => [...document.querySelectorAll('a[href^="/agent/"]')]
                     .map(a => ({href: a.getAttribute('href'),
                                 title: (a.innerText || '').trim().split('\\n')[0]}))
                     .filter(x => x.title && x.href.split('/').length > 2)
                     .slice(0, limit)""", limit)
        except PWError:
            raw = []
        seen, out = set(), []
        for item in raw:
            if item["href"] in seen:
                continue
            seen.add(item["href"])
            out.append({"chat_id": item["href"].rsplit("/", 1)[-1], "title": item["title"],
                        "url": self.cfg["base_url"].rstrip("/") + item["href"]})
        return out

    # ---- 输入 & 发送 ---------------------------------------------------------
    def fill_editor(self, text: str) -> None:
        """往 Tiptap/ProseMirror 里写内容：优先模拟粘贴（支持多行/长文本），失败退回逐字输入。"""
        assert self.page is not None
        page = self.page
        editor = self.wait_editor()
        editor.click()
        jitter()

        # 1) 清空已有内容
        try:
            page.keyboard.press("ControlOrMeta+a")
            page.keyboard.press("Delete")
        except PWError:
            pass

        # 2) 粘贴事件（ProseMirror 会把纯文本转成段落）
        try:
            page.evaluate(
                """(text) => {
                    const el = document.activeElement;
                    const dt = new DataTransfer();
                    dt.setData('text/plain', text);
                    el.dispatchEvent(new ClipboardEvent('paste', {
                        clipboardData: dt, bubbles: true, cancelable: true
                    }));
                }""",
                text,
            )
            page.wait_for_timeout(300)
            got = norm(editor.inner_text())
            if got and norm(text)[:24] in got:
                log("已写入输入框（paste 方式）")
                return
        except PWError as exc:
            log(f"paste 方式失败（{exc}），改用键盘输入")

        # 3) 键盘输入兜底；多行用 Shift+Enter 换行，避免直接触发发送
        editor.click()
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if i:
                page.keyboard.press("Shift+Enter")
            page.keyboard.insert_text(line)
            jitter(0.01, 0.05)
        log("已写入输入框（键盘方式）")

    def submit(self) -> None:
        """点发送按钮；按钮不可用时退回 Enter。"""
        assert self.page is not None
        page = self.page
        btn = first_visible(page, self.cfg["send_selectors"])
        if btn is not None:
            try:
                if btn.is_enabled():
                    btn.click()
                    log("已点击发送")
                    return
            except PWError:
                pass
        editor = self.wait_editor(5)
        editor.click()
        page.keyboard.press("Enter")
        log("已按 Enter 发送")

    # ---- 状态判定 -----------------------------------------------------------
    def message_count(self) -> int:
        assert self.page is not None
        for selectors in (self.cfg.get("turn_selectors") or [],):
            try:
                n = len(visible_of(self.page, selectors))
                if n:
                    return n
            except PWError:
                pass
        try:
            return len(visible_of(self.page, self.cfg["user_selectors"])) + len(
                visible_of(self.page, self.cfg["assistant_selectors"]))
        except PWError:
            return 0

    def is_streaming(self) -> bool:
        assert self.page is not None
        for sel in self.cfg["streaming_selectors"]:
            try:
                loc = self.page.locator(sel)
                if loc.count() and loc.first.is_visible():
                    return True
            except PWError:
                continue
        return False

    @staticmethod
    def raise_http_failure(failures: list[tuple[int, str]]) -> None:
        """把抓到的接口失败翻译成错误码（429 = 限流最常见）。"""
        for status, url in failures:
            if status == 429:
                raise SkillError(
                    "RATE_LIMITED",
                    f"Arena 拒绝了新的会话请求（HTTP 429，{url}）：请求过多或额度用尽",
                )
            if status in (401, 403):
                raise SkillError(
                    "LOGIN_REQUIRED",
                    f"会话被服务端拒绝（HTTP {status}，{url}）：cookies 过期或不是登录态",
                )
        if failures:
            status, url = failures[0]
            raise SkillError("SEND_FAILED", f"Arena 接口返回 HTTP {status}（{url}），消息没送出去")

    def has_transcript(self) -> bool:
        """页面上是否已经出现了对话区（说明这是聊天页，而不是落地页/空状态）。"""
        assert self.page is not None
        for sel in (self.cfg.get("turn_selectors") or []):
            try:
                if self.page.locator(sel).count():
                    return True
            except PWError:
                continue
        return False

    # ---- 回答抽取 -----------------------------------------------------------
    def extract_answer(self, prompt: str, baseline: str) -> str:
        """三级策略：结构化选择器 -> 文本差分 -> 整页文本。"""
        assert self.page is not None
        page = self.page

        # A. 结构化：取最后一个可见的 assistant 容器
        cands = visible_of(page, self.cfg["assistant_selectors"])
        if cands:
            for cand in reversed(cands):          # 从最后一条往前找，跳过"其实是用户消息"
                try:
                    text = clean_answer(cand.inner_text(), self.cfg)
                except PWError:
                    continue
                if not text:
                    continue
                if norm(text) == norm(prompt):    # 命中的是用户自己那条 → 说明回答还没出来
                    continue
                if text.strip().lower() in UI_TEXT_BLACKLIST:   # "Agent ⌄" 这类控件文案
                    continue
                if len(text) > 1:
                    return text

        # A2. 已经有对话区、但还没出现 assistant 回合 → 说明仍在生成/跳转中。
        #     这时绝不能退到整页差分（会抽到 "Agent"、侧边栏标题之类的噪音）。
        if self.has_transcript():
            return ""

        # B. 文本差分：取用户消息之后新出现的文字
        now = body_text(page)
        key = norm(prompt)[:60]
        idx = now.rfind(key)
        if idx != -1:
            tail = now[idx + len(key):]
            tail = clean_answer(tail, self.cfg)
            if len(tail) > 1:
                return tail

        # C. 兜底：整页新增文本
        base = set(line.strip() for line in baseline.splitlines() if line.strip())
        new_lines = [ln.strip() for ln in now.splitlines()
                     if ln.strip() and ln.strip() not in base]
        return clean_answer("\n".join(new_lines), self.cfg)

    # ---- 主流程：提问 -------------------------------------------------------
    def ask(self, prompt: str, timeout: Optional[float] = None,
            stable: Optional[float] = None, chat: Optional[str] = None,
            on_update: Optional[Any] = None,
            poll: Optional[float] = None) -> dict[str, Any]:
        """提问并取回回答。

        on_update(text)：每当下抽到更长的回答就回调一次（用来做流式输出 / 进度日志）。
        poll：轮询间隔秒数（流式时调小一点更跟手）。
        """
        timeout = timeout or self.cfg["wait_timeout"]
        stable = stable or self.cfg["stable_seconds"]
        poll_seconds = float(poll or self.cfg["poll_seconds"])
        assert self.page is not None
        page = self.page

        if chat:                                # 在已有会话里继续提问
            self.open_chat(chat)
        else:
            self.open_agent()
        baseline = body_text(page)
        editor = self.wait_editor()
        self.fill_editor(prompt)
        before_msgs = self.message_count()
        http_failures: list[tuple[int, str]] = []

        def _on_response(resp: Any) -> None:
            try:
                if resp.status >= 400 and any(m in resp.url for m in CHAT_API_MARKERS):
                    http_failures.append((resp.status, resp.url.split("?")[0][:120]))
            except PWError:
                pass

        try:
            page.on("response", _on_response)
        except PWError:
            pass
        self.submit()

        # 1) 判断是否被登录墙拦住（匿名状态下消息不会真的发出去）
        def wait_sent(seconds: float) -> bool:
            deadline_ = time.time() + seconds
            while time.time() < deadline_:
                self.raise_http_failure(http_failures)
                if self.login_dialog_visible():
                    raise SkillError(
                        "LOGIN_REQUIRED",
                        "发送被登录弹窗拦截：当前会话未登录或已过期，请先运行 `login` 或导入 cookies",
                    )
                try:
                    if norm(editor.inner_text()) == "":
                        return True
                except PWError:
                    return True
                if self.message_count() > before_msgs:
                    return True
                page.wait_for_timeout(400)
            return False

        sent = wait_sent(20)
        if not sent:
            # SPA 还没 hydrate 完就点发送时，点击会被吞掉：重填一次再发（这次页面已经活了）
            log("发送似乎没生效，重试一次…")
            try:
                if norm(editor.inner_text()) == "":
                    self.fill_editor(prompt)
            except PWError:
                pass
            self.submit()
            sent = wait_sent(20)
        if not sent:
            raise SkillError(
                "SEND_FAILED",
                "消息没有被提交（输入框仍有内容，也没有出现新消息）；"
                "页面可能还没加载完或改版了，试试 `--headed` 观察，或稍后重试",
            )

        log("消息已提交，等待 Agent 执行…")

        # 2) 等待流式输出结束（文本稳定 + 无 stop 按钮）
        started = time.time()
        deadline = started + timeout
        last_text, last_change = "", time.time()
        answer = ""
        seen_transcript = False
        while time.time() < deadline:
            page.wait_for_timeout(int(poll_seconds * 1000))
            self.raise_http_failure(http_failures)
            if self.has_transcript():
                seen_transcript = True
            elif seen_transcript and not self.message_count() and extract_chat_id(page.url) is None:
                # 消息先乐观渲染、随后整条被页面撤回：服务端拒绝了这次会话（限流时就是这样）
                self.raise_http_failure(http_failures or [(429, "create-chat")])
                raise SkillError("SEND_FAILED",
                                 "消息发出去后又被页面撤回（服务端拒绝），过一会儿再试")
            answer = self.extract_answer(prompt, baseline)
            if answer != last_text:
                last_text, last_change = answer, time.time()
                if on_update is not None and answer:
                    try:
                        on_update(answer)
                    except Exception:  # noqa: BLE001  回调出错不能影响主流程
                        pass
            else:
                if answer and not self.is_streaming() and time.time() - last_change >= stable:
                    log(f"输出已稳定（{time.time() - started:.0f}s）")
                    break
            for marker in self.cfg["error_markers"]:
                if marker.lower() in answer.lower():
                    log(f"页面报错标记：{marker}")
        else:
            raise SkillError("TIMEOUT", f"等待 {timeout:.0f}s 仍未拿到完整回答")

        answer = clean_answer(answer or last_text, self.cfg)
        if not answer:
            raise SkillError("UI_CHANGED", "没有抽取到回答文本，请检查 assistant_selectors 配置")

        # 3) 关掉可能残留的弹窗、保存会话
        try:
            page.remove_listener("response", _on_response)
        except PWError:
            pass
        self.handle_terms()
        return {
            "ok": True,
            "prompt": prompt,
            "answer": answer,
            "url": page.url,
            "chat_id": extract_chat_id(page.url),
            "elapsed_seconds": round(time.time() - started, 1),
            "reply_markdown": answer,  # 上层可直接转发给用户
        }


def extract_chat_id(url: str) -> Optional[str]:
    m = re.search(r"/agent/(?:c/)?([0-9a-zA-Z-]{8,})", url)
    return m.group(1) if m else None


# 这些文本出现在页面上，但绝不是回答（聊天页的控件/占位文案）
UI_TEXT_BLACKLIST = {
    "agent", "arena", "new chat", "search", "leaderboard", "thinking", "working",
    "generating", "loading", "sending", "connectors", "today", "yesterday",
}

_TS_PATTERNS = (
    re.compile(r"^\d+(?:\.\d+)?\s*(?:s|sec|secs|m|min|mins|h)\s*\|\s*.+$", re.I),
    re.compile(r"^(?:just now|\d+\s+(?:second|minute|hour|day)s?\s+ago)$", re.I),
    re.compile(r"^\d{1,2}:\d{2}\s*(?:AM|PM)$", re.I),
)


def _is_timestamp_line(line: str) -> bool:
    return any(p.match(line) for p in _TS_PATTERNS)


def clean_answer(text: str, cfg: dict[str, Any]) -> str:
    if not text:
        return ""
    noise = set(n.lower() for n in cfg.get("noise_lines", []))
    lines = []
    for raw in text.replace("\u200b", "").splitlines():
        s = raw.strip()
        if not s:
            lines.append("")
            continue
        if s.lower() in noise or _is_timestamp_line(s):
            continue
        lines.append(s)
    out = "\n".join(lines)
    # 截掉页面上混进来的 UI 区块（反馈条、Workspace 面板、Connectors 列表等）
    for marker in cfg.get("answer_stop_markers", []):
        idx = out.find(marker)
        if idx > 0:
            out = out[:idx]
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


# --------------------------------------------------------------------------------------
# 错误类型
# --------------------------------------------------------------------------------------
class SkillError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# 这些接口被拒绝（4xx/5xx），基本等于「这条消息没送出去」
CHAT_API_MARKERS = ("create-chat", "/stream/", "nextjs-api", "/api/chat")

EXIT_CODES = {
    "RATE_LIMITED": 7,
    "LOGIN_REQUIRED": 2,
    "SEND_FAILED": 3,
    "TIMEOUT": 4,
    "UI_CHANGED": 5,
    "BLOCKED": 6,
    "LOGIN_FAILED": 2,
    "LOGIN_TIMEOUT": 2,
    "BAD_INPUT": 1,
}


# --------------------------------------------------------------------------------------
# 命令实现
# --------------------------------------------------------------------------------------
def cmd_check(session: Session, probe: bool = False,
              probe_text: str = "hi") -> dict[str, Any]:
    """判断会话是否可用。

    三种结果：
      logged_in = True   发现了只有登录后才会出现的账号入口 + 没有登录弹窗
      logged_in = False  可见登录弹窗（确定未登录）
      logged_in = None   看不出来（推荐加 --probe：真发一条消息，看会不会被登录墙拦下）
    """
    page = session.open_agent()
    jar = session.ctx.cookies() if session.ctx else []
    patterns = [p.lower() for p in session.cfg.get("auth_cookie_patterns", [])]
    base = {
        "cookie_count": len(jar),
        "auth_cookie_names": [c["name"] for c in jar
                              if any(p in c["name"].lower() for p in patterns)][:8],
        "state_file": str(session.state_file),
        "profile_dir": str(session.profile_dir),
        "cookies": len(session.ctx.cookies()) if session.ctx else 0,
        "url": page.url,
    }

    if session.login_dialog_visible():
        return {"ok": True, "logged_in": False, "hint": "确定未登录：请运行 `login` 或导入 cookies", **base}

    if probe:
        editor = session.wait_editor()
        session.fill_editor(probe_text)
        session.submit()
        deadline = time.time() + 15
        while time.time() < deadline:
            if session.login_dialog_visible():
                return {"ok": True, "logged_in": False, "probe": True,
                        "hint": "发送被登录墙拦截，请重新登录", **base}
            try:
                if norm(editor.inner_text()) == "":
                    return {"ok": True, "logged_in": True, "probe": True,
                            "hint": "探测消息已发出（会产生一条聊天记录）", **base}
            except PWError:
                break
            page.wait_for_timeout(500)
        return {"ok": True, "logged_in": None, "probe": True,
                "hint": "探测结果不明确，请用 `ask` 实测", **base}

    txt = body_text(page)
    anon = session.cfg.get("anonymous_markers") or []
    if any(m in txt for m in anon):
        return {"ok": True, "logged_in": False,
                "hint": "页面是未登录的落地页（关闭 cookies 或已失效），请重新导入 cookies", **base}

    if first_visible(page, session.cfg.get("logged_in_selectors") or []) is not None:
        return {"ok": True, "logged_in": True,
                "hint": "检测到历史会话侧边栏，会话有效", **base}

    if first_visible(page, session.cfg["account_selectors"]) is not None:
        return {"ok": True, "logged_in": True, "hint": "检测到账号入口，会话看起来有效", **base}

    return {"ok": True, "logged_in": None,
            "hint": "无法确认登录态；加 --probe 会真发一条消息来验证（会留下聊天记录）", **base}


def cmd_login(session: Session, email: Optional[str], code: Optional[str],
              wait_s: float, probe_text: str) -> dict[str, Any]:
    """触发并完成登录，然后保存会话。

    流程（实测 arena.ai 的登录路径）：
      进入 /agent -> 首次会弹 Terms(点 Agree) -> 点发送才会弹 "Log In or Create Account"
      -> 填邮箱 -> Continue with email -> 邮件里点 magic link（或填验证码）-> 登录成功
    """
    page = session.open_agent()

    if not session.login_dialog_visible():
        # 通过"发一条试探消息"来触发登录弹窗
        session.fill_editor(probe_text)
        session.submit()
        deadline = time.time() + 15
        while time.time() < deadline and not session.login_dialog_visible():
            page.wait_for_timeout(500)
        session.handle_terms()

    if not session.login_dialog_visible():
        # 没有弹登录框：可能本来就已经登录了
        session.save_state()
        return {"ok": True, "already_logged_in": True, "state_file": str(session.state_file)}

    if email:
        field = first_visible(page, session.cfg["email_input_selectors"])
        if field is None:
            raise SkillError("UI_CHANGED", "找不到登录弹窗里的邮箱输入框")
        field.click()
        field.fill(email)
        jitter()
        hit = click_text(page, session.cfg["continue_email_texts"])
        log(f"已提交邮箱（{hit or '按钮未命中'}），请查收邮件里的登录链接"
            + ("，或把验证码用 --code 传进来" if not code else ""))
        page.wait_for_timeout(2500)
        err = body_text(page)
        for marker in ("not permitted", "invalid", "Enter a valid"):
            if marker.lower() in err.lower():
                raise SkillError("LOGIN_FAILED", f"邮箱被拒绝：{marker}")

    if code:
        field = first_visible(page, session.cfg["code_input_selectors"])
        if field is None:
            raise SkillError("UI_CHANGED", "找不到验证码输入框")
        field.click()
        page.keyboard.insert_text(code)
        page.wait_for_timeout(500)
        click_text(page, ["Verify", "Continue", "Submit", "Sign in"])
        log("已提交验证码")
    else:
        log(f"等待登录完成（最多 {wait_s:.0f}s）：请在同一浏览器窗口里点开邮件链接 / 输入验证码")

    # 轮询直到登录成功（登录弹窗消失 & 输入框可用）
    deadline = time.time() + wait_s
    while time.time() < deadline:
        page.wait_for_timeout(1000)
        if not session.login_dialog_visible() and first_visible(page, session.cfg["editor_selectors"]):
            session.save_state()
            # 把试探消息清掉，避免留下垃圾会话
            try:
                editor = first_visible(page, session.cfg["editor_selectors"])
                if editor:
                    editor.click()
                    page.keyboard.press("ControlOrMeta+a")
                    page.keyboard.press("Delete")
            except PWError:
                pass
            return {"ok": True, "logged_in": True, "state_file": str(session.state_file),
                    "cookies": len(session.ctx.cookies()) if session.ctx else 0}
    raise SkillError("LOGIN_TIMEOUT", f"{wait_s:.0f}s 内没有检测到登录完成")


def cmd_export(session: Session, out: Path) -> dict[str, Any]:
    assert session.ctx is not None
    state = session.ctx.storage_state()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(out, 0o600)
    return {"ok": True, "exported": str(out), "cookies": len(state.get("cookies", []))}


SAME_SITE = {"lax": "Lax", "strict": "Strict", "none": "None", "no_restriction": "None"}
NETSCAPE_HINT = "Netscape HTTP Cookie File"


def _norm_cookie(c: dict[str, Any]) -> Optional[dict[str, Any]]:
    """统一成 Playwright add_cookies() 能吃的形状；顺手做几处常见格式的兼容。"""
    name = (c.get("name") or "").strip()
    if not name:
        return None
    value = c.get("value")
    value = "" if value is None else str(value)

    domain = (c.get("domain") or "").strip()
    path = (c.get("path") or "/").strip() or "/"
    exp = c.get("expires", c.get("expirationDate", c.get("expiry", -1)))
    try:
        expires = float(exp) if exp not in (None, "", 0, "0") else -1.0
    except (TypeError, ValueError):
        expires = -1.0
    if expires != -1 and expires < 1_000_000_000:      # 个别扩展给的是毫秒
        if expires > 10_000_000_000:
            expires /= 1000.0

    out: dict[str, Any] = {
        "name": name,
        "value": value,
        "path": path,
        "expires": expires,
        "httpOnly": bool(c.get("httpOnly", False)),
        "secure": bool(c.get("secure", False)),
        "sameSite": SAME_SITE.get(str(c.get("sameSite", "")).lower(), "Lax"),
    }
    if domain:
        out["domain"] = domain if domain.startswith(".") else domain
    elif c.get("url"):
        out["url"] = c["url"]
    else:
        return None
    return out


def parse_cookie_file(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """解析 cookies 文件，自动识别三种常见格式：
      1. Netscape cookies.txt（curl / 浏览器扩展导出，制表符分隔，可有 #HttpOnly_ 前缀）
      2. EditThisCookie / Cookie-Editor 的 JSON 数组
      3. Playwright storage_state（{"cookies": [...], "origins": [...]}）
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SkillError("BAD_INPUT", f"读不到 cookies 文件：{exc}") from exc
    head = text.lstrip()[:200]

    # --- JSON ---
    if head.startswith(("{", "[")):
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SkillError("BAD_INPUT", f"cookies JSON 解析失败：{exc}") from exc
        if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
            cookies = [c for c in (_norm_cookie(x) for x in raw["cookies"]) if c]
            return cookies, {"format": "storage_state", "origins": raw.get("origins") or []}
        if isinstance(raw, list):
            cookies = [c for c in (_norm_cookie(x) for x in raw) if c]
            return cookies, {"format": "json_array", "origins": []}
        raise SkillError("BAD_INPUT", "JSON 格式不对：既不是 cookies 数组，也没有 cookies 字段")

    # --- Netscape cookies.txt ---
    cookies: list[dict[str, Any]] = []
    skipped = 0
    for line in text.splitlines():
        raw = line.rstrip("\r\n")
        if not raw.strip():
            continue
        http_only = raw.startswith("#HttpOnly_")
        if raw.startswith("#") and not http_only:
            continue                      # 注释行（含标准文件头）
        fields = (raw[len("#HttpOnly_"):] if http_only else raw).split("\t")
        if len(fields) < 7:
            skipped += 1
            continue
        domain, _dflag, cpath, secure, expires, name, value = fields[:7]
        c = _norm_cookie({
            "name": name, "value": value, "domain": domain, "path": cpath,
            "secure": str(secure).upper() == "TRUE",
            "httpOnly": http_only,
            "expires": expires,
        })
        if c:
            cookies.append(c)
        else:
            skipped += 1

    if not cookies:
        raise SkillError(
            "BAD_INPUT",
            "这不像一份 cookies 文件。支持的格式：Netscape cookies.txt（curl/扩展导出）、"
            "EditThisCookie JSON 数组、Playwright storage_state",
        )
    return cookies, {"format": "netscape", "skipped": skipped,
                     "had_header": NETSCAPE_HINT in text}


def summarize_cookies(cookies: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    """cookie 体检：有多少条、哪些是登录凭证、有没有过期。只统计不泄露值。"""
    now = time.time()
    patterns = [p.lower() for p in cfg.get("auth_cookie_patterns", [])]
    auth = [c["name"] for c in cookies if any(p in c["name"].lower() for p in patterns)]
    expired = [c["name"] for c in cookies if c.get("expires", -1) not in (-1, 0) and c["expires"] < now]
    domains = sorted({c.get("domain", "") for c in cookies})
    return {
        "count": len(cookies),
        "domains": domains,
        "auth_cookie_names": auth[:8],
        "expired_names": expired[:8],
        "has_auth_cookie": bool(auth),
    }


def cmd_import(path: Path, state_file: Path, cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """把用户上传的 cookies 文件变成可用的会话快照。"""
    cookies, meta = parse_cookie_file(path)
    state = {"cookies": cookies, "origins": meta.get("origins") or []}
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(state_file, 0o600)
    except OSError:
        pass

    health = summarize_cookies(cookies, cfg or DEFAULT_CONFIG)
    result: dict[str, Any] = {
        "ok": True,
        "source": str(path),
        "format": meta.get("format"),
        "state_file": str(state_file),
        **health,
    }
    if not health["has_auth_cookie"]:
        result["warning"] = ("没找到看起来像登录凭证的 cookie（可调 config.auth_cookie_patterns）；"
                             "若 ask 时报 LOGIN_REQUIRED，请重新导出 cookies")
    if health["expired_names"]:
        result.setdefault("warning", "")
        result["warning"] += ("；部分 cookie 已过期：" + ", ".join(health["expired_names"]))
    return result


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def _add_common_args(p: argparse.ArgumentParser) -> None:
    """全局参数。用 SUPPRESS 作为默认值，这样写在子命令前、后都生效。"""
    S = argparse.SUPPRESS
    p.add_argument("--config", default=S, help="配置文件路径（JSON，覆盖默认选择器）")
    p.add_argument("--base-url", default=S, help="站点根地址，默认 https://arena.ai")
    p.add_argument("--profile-dir", default=S, help="浏览器用户目录（默认 ~/.arena-agent/prof）")
    p.add_argument("--state-file", default=S, help="会话快照文件（默认 ~/.arena-agent/state.json）")
    p.add_argument("--cookies", default=S,
                   help="直接用一份 cookies 文件（Netscape cookies.txt / JSON），先导入再执行命令")
    p.add_argument("--headed", action="store_true", default=S, help="显示浏览器窗口（过验证码/调试）")
    p.add_argument("--channel", default=S, help='浏览器通道，如 "chrome"（用本机真实 Chrome）')
    p.add_argument("--user-agent", default=S, help="覆盖 User-Agent")
    p.add_argument("--timeout", type=float, default=S, help="等待回答的超时秒数（默认 900）")
    p.add_argument("--stable", type=float, default=S, help="文本稳定多少秒算生成结束（默认 2.5）")
    p.add_argument("--format", choices=["json", "text", "markdown"], default=S,
                   help="输出格式（默认 json）")
    p.add_argument("--out", default=S, help="把结果同时写入文件")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_args(common)

    p = argparse.ArgumentParser(
        prog="arena_agent",
        description="用浏览器驱动 arena.ai/agent：登录并记住 cookies、发消息、取回 Agent 结果",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    _add_common_args(p)

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", parents=[common], help="检查会话是否仍有效")

    ck_parser = sub.choices["check"]
    ck_parser.add_argument("--probe", action="store_true",
                           help="真发一条探测消息来确认登录态（会在会话里留下一条记录）")
    ck_parser.add_argument("--probe-text", default="hi")

    a = sub.add_parser("ask", parents=[common], help="发送一条消息并取回回答")
    a.add_argument("prompt", nargs="?", help="要发给 Agent 的内容；传 - 表示从 stdin 读取")
    a.add_argument("--file", help="从文件读取 prompt")

    lg = sub.add_parser("login", parents=[common], help="登录并保存 cookies")
    lg.add_argument("--email", help="用于接收登录链接/验证码的邮箱")
    lg.add_argument("--code", help="一次性验证码（如页面要求）")
    lg.add_argument("--wait", type=float, default=240, help="等待人工完成登录的秒数")
    lg.add_argument("--probe-text", default="hi", help="用于触发登录弹窗的占位消息")

    e = sub.add_parser("export-cookies", parents=[common], help="导出当前会话 cookies")
    e.add_argument("--cookies-out", default=None,
                   help="导出路径（默认 ~/.arena-agent/cookies.json；注意别和 --out 混用）")

    i = sub.add_parser("import-cookies", parents=[common], help="导入浏览器导出的 cookies JSON")
    i.add_argument("path", help="cookies JSON 文件（EditThisCookie / storage_state 格式）")
    return p


def emit(result: dict[str, Any], fmt: str, out: Optional[str]) -> None:
    if fmt == "json":
        payload = json.dumps(result, ensure_ascii=False, indent=2)
    elif fmt == "text":
        payload = result.get("answer") or result.get("error") or json.dumps(result, ensure_ascii=False)
    else:  # markdown：直接可转发给用户
        if result.get("ok"):
            payload = result.get("reply_markdown") or result.get("answer", "")
        else:
            payload = f"> ⚠️ {result.get('code')}: {result.get('error')}"
    print(payload)
    if out:
        Path(out).write_text(payload, encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    g = lambda name, default=None: getattr(args, name, default)  # noqa: E731

    cfg = load_config(g("config"), {
        "base_url": g("base_url"),
        "wait_timeout": g("timeout"),
        "stable_seconds": g("stable"),
    })
    fmt = g("format", "json")
    out = g("out")
    state_file = Path(g("state_file") or (DEFAULT_DIR / "state.json")).expanduser()
    profile_dir = Path(g("profile_dir") or (DEFAULT_DIR / "prof")).expanduser()

    if g("cookies"):
        # 用户上传了 cookies 文件：先导入，再照常执行后面的命令
        imported = cmd_import(Path(g("cookies")).expanduser(), state_file, cfg)
        log(f"已从 {imported['source']} 导入 {imported['count']} 条 cookies"
            f"（登录凭证: {', '.join(imported['auth_cookie_names']) or '未识别'}）")
        if imported.get("warning"):
            log(f"注意：{imported['warning']}")

    try:
        if args.cmd == "import-cookies":
            emit(cmd_import(Path(args.path).expanduser(), state_file, cfg), "json", out)
            return 0

        with Session(cfg, profile_dir, state_file,
                     headed=bool(g("headed")), channel=g("channel"),
                     user_agent=g("user_agent")) as session:
            if args.cmd == "check":
                emit(cmd_check(session, probe=bool(getattr(args, "probe", False)),
                               probe_text=getattr(args, "probe_text", "hi")), fmt, out)
            elif args.cmd == "login":
                res = cmd_login(session, args.email, args.code, args.wait, args.probe_text)
                emit(res, fmt, out)
            elif args.cmd == "export-cookies":
                target = Path(getattr(args, "cookies_out", None) or g("out")
                              or (DEFAULT_DIR / "cookies.json"))
                emit(cmd_export(session, target), "json", None)
            elif args.cmd == "ask":
                prompt = args.prompt or ""
                if getattr(args, "file", None):
                    prompt = Path(args.file).read_text(encoding="utf-8")
                elif prompt == "-":
                    prompt = sys.stdin.read()
                if not prompt.strip():
                    raise SkillError("BAD_INPUT", "prompt 为空（用法：ask \"内容\" / ask --file f.md / echo x | ask -）")
                res = session.ask(prompt, timeout=g("timeout"), stable=g("stable"))
                emit(res, fmt, out)
        return 0
    except SkillError as exc:
        emit({"ok": False, "code": exc.code, "error": exc.message}, fmt, out)
        return EXIT_CODES.get(exc.code, 1)
    except KeyboardInterrupt:
        emit({"ok": False, "code": "INTERRUPTED", "error": "用户中断"}, fmt, out)
        return 130
    except PWError as exc:
        emit({"ok": False, "code": "BROWSER_ERROR", "error": str(exc).splitlines()[0]}, fmt, out)
        return 1


if __name__ == "__main__":
    sys.exit(main())
