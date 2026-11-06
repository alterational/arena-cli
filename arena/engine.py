#!/usr/bin/env python3
"""
arena_agent.py — drives https://arena.ai/agent with a "real browser"

Capabilities:
  1. Open arena.ai/agent (Chromium driven by Playwright, the same engine underlying browser-use)
  2. Log in (email magic link / one-time code) and persist cookies + localStorage
  3. Write the user's input into the page's Agent input box (Tiptap / ProseMirror)
  4. Submit and wait for Agent Mode to finish (automatically detects the end of streaming)
  5. Retrieve the answer, output it as JSON / plain text / Markdown, and pass it to the upper layer (Hermes) to forward to the user

Commands:
  login            Interactive login; saves the session (cookies)
  ask "question"   Send one message, wait for the result, and print the answer
  check            Check whether the currently saved session is still valid
  export-cookies   Export the current session as cookies JSON
  import-cookies   Import a session from cookies JSON exported by a browser

Common options:
  --base-url https://arena.ai      Target site (can point to the mock service during self-tests)
  --profile-dir ~/.arena-cli/prof   Browser user-data directory (persistent; the most reliable way to "remember the login")
  --state-file  ~/.arena-cli/state.json  Session snapshot (cookies + localStorage; portable)
  --headed                         Show the browser window (for debugging / solving verification manually)
  --json / --text / --markdown     Output format

Exit codes:
  0 success
  2 login required (LOGIN_REQUIRED)
  3 send failed (SEND_FAILED)
  4 wait timed out (TIMEOUT)
  5 abnormal page state (UI_CHANGED; selectors need updating)
  6 blocked by human verification (BLOCKED; needs --headed for manual completion / imported cookies)
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
except ImportError:  # If playwright is not installed, the CLI must still run offline commands such as doctor/config/cookies
    PLAYWRIGHT_AVAILABLE = False
    sync_playwright = None  # type: ignore[assignment]
    Page = BrowserContext = Locator = object  # type: ignore[assignment,misc]

    class PWError(Exception):  # type: ignore[no-redef]
        pass

    class PWTimeout(PWError):  # type: ignore[no-redef]
        pass

HOME = Path.home()
DEFAULT_DIR = HOME / ".arena-cli"

# --------------------------------------------------------------------------------------
# Default configuration: every selector can be overridden in config.json; when the page changes, only the config needs updating, not the code
# --------------------------------------------------------------------------------------
DEFAULT_CONFIG: dict[str, Any] = {
    "base_url": "https://arena.ai",
    "proxy": "",                 # http://host:port / socks5://host:port / user:pass also works
    "block_analytics_scripts": True,
    "reduce_visual_effects": True,
    "agent_path": "/agent",
    # Input box (composer). arena.ai currently uses a Tiptap/ProseMirror editor
    "editor_selectors": [
        'div.tiptap.ProseMirror[contenteditable="true"]',
        '.ProseMirror[contenteditable="true"]',
        'div[contenteditable="true"][role="textbox"]',
        'textarea[placeholder]',
    ],
    # Send button
    "send_selectors": [
        'button[aria-label="Send message"]',
        'button[aria-label*="Send" i]',
        'button[type="submit"]',
    ],
    # Signs of streaming output / something that can be stopped (if present, the answer is not yet complete)
    "streaming_selectors": [
        'button[aria-label*="Stop" i]',
        '[data-streaming="true"]',
        '[data-state="streaming"]',
    ],
    # Conversation turns (one each for user and assistant per round)
    "turn_selectors": ["[data-agent-transcript-message]"],
    # Containers for the answer body (tried in order; uses the last visible match)
    # Verified on arena.ai: an assistant turn = [data-agent-transcript-message] without [data-user-message-body-row]
    #              body = the .prose inside that turn (timestamps / feedback buttons are outside .prose, so they are naturally excluded)
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
    # User message container (used to judge whether the message was actually sent)
    "user_selectors": [
        '[data-user-message-body-row]',
        '[data-message-author-role="user"]',
        '[data-role="user"]',
        '[data-testid*="user"]',
        '.message-user',
    ],
    # If these "page UI strings" appear in extracted text, truncate there (fallback strategy)
    "answer_stop_markers": [
        "Was this task successful?",
        "Keep working",
        "All files that are created or uploaded in this session",
        "Stop response",
    ],
    # Conversation workspace (Workspace panel): located automatically via DOM features by default; can be overridden here when the page changes
    "workspace_panel_selectors": [],
    "workspace_expand_clicks": 60,       # Maximum number of collapsed folders to expand (prevents hanging with thousands of files)
    # After the session ends, Arena replaces the input box with a "Was this task successful?" feedback card (dismissable with Esc / ×);
    # the composer only returns after it is closed — multi-turn follow-ups must close this card first
    "feedback_text": "Was this task successful?",
    "feedback_dismiss_selectors": [
        'button[aria-label*="Close" i]',
        'button[aria-label*="Dismiss" i]',
        'button:has-text("Esc")',
    ],
    # Dialog text features
    "terms_dialog_text": "Terms of Use",
    "terms_agree_texts": ["Agree", "I Agree", "同意"],  # "同意" = Chinese-locale label for "Agree", kept so the match still works
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
    # UI noise to strip from the answer area (button labels, etc.)
    "noise_lines": [
        "Copy", "Copied", "Retry", "Regenerate", "Edit", "Share", "Like", "Dislike",
        "Sources", "Thought for", "Show more", "Show less", "Stop",
    ],
    # Cookie name patterns used to determine whether the cookies contain a login session
    "auth_cookie_patterns": ["arena-auth", "__session", "next-auth", "session", "auth", "token"],
    # Account entry points that only appear after login (used by check)
    # Text features of the logged-out landing page / sidebar entries that only appear after login
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
    # Text features of Cloudflare and similar "human verification" interstitial pages
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
    # Behavior parameters
    "wait_timeout": 900,        # Overall timeout for waiting for the answer (seconds)
    "stable_seconds": 2.5,      # Number of seconds the text must stay unchanged before generation is considered finished
    "poll_seconds": 1.0,
    "login_timeout": 240,       # Seconds the login command waits for manual login to complete
    "viewport": {"width": 1440, "height": 900},
    "locale": "en-US",
}


def load_config(explicit: Optional[str], overrides: dict[str, Any]) -> dict[str, Any]:
    """Default config <- /home/user/.arena-cli/config.json <- --config file <- command-line overrides"""
    cfg = dict(DEFAULT_CONFIG)
    for path in [DEFAULT_DIR / "config.json", Path(explicit) if explicit else None]:
        if path and Path(path).exists():
            cfg.update(json.loads(Path(path).read_text(encoding="utf-8")))
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg


# --------------------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------------------
def log(msg: str) -> None:
    sys.stderr.write(f"[arena] {msg}\n")
    sys.stderr.flush()


def parse_proxy(url: Optional[str]) -> Optional[dict[str, Any]]:
    """`http://user:pass@host:port` / `socks5://host:port` -> Playwright proxy parameters."""
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = "http://" + url
    from urllib.parse import unquote, urlparse

    try:
        u = urlparse(url)
        if not u.hostname:
            raise ValueError
        scheme = (u.scheme or "http").lower()
        if scheme not in ("http", "https", "socks5", "socks4"):
            raise SkillError("BAD_INPUT", "Unsupported proxy protocol (use http, https, socks5 or socks4)")
        port = u.port or (1080 if scheme.startswith("socks") else 8080)
    except ValueError:
        raise SkillError("BAD_INPUT", "Unrecognized proxy address") from None
    proxy: dict[str, Any] = {"server": f"{scheme}://{u.hostname}:{port}"}
    if u.username:
        proxy["username"] = unquote(u.username)
    if u.password:
        proxy["password"] = unquote(u.password)
    return proxy


def mask_proxy(url: Optional[str]) -> str:
    """Mask proxy credentials and query parameters in logs and output."""
    from urllib.parse import urlsplit, urlunsplit
    url = (url or "").strip()
    if not url:
        return ""
    try:
        has_scheme = "://" in url
        u = urlsplit(url if has_scheme else "http://" + url)
        if not u.hostname:
            return "[redacted proxy]"
        host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
        netloc = ("***@" if "@" in u.netloc else "") + host + (f":{u.port}" if u.port else "")
        return urlunsplit((u.scheme, netloc, "", "", "")) if has_scheme else netloc
    except ValueError:
        return "[redacted proxy]"


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
    """Click a button or link by its accessible name / text; returns the matched text."""
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
        try:  # Fallback: any visible element containing this text
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
# Browser session: profile directory (persistent) + state.json (portable snapshot)
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

    # ---- Lifecycle -------------------------------------------------------------
    def __enter__(self) -> "Session":
        if not PLAYWRIGHT_AVAILABLE:
            raise SkillError(
                "DEPENDENCY_MISSING",
                "Playwright is missing: pip install playwright && python3 -m playwright install chromium",
            )
        self.profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        proxy = parse_proxy(self.cfg.get("proxy"))
        if proxy:
            log(f"Using proxy: {mask_proxy(str(self.cfg.get('proxy')))}")
            # Chrome by default adds localhost to the proxy bypass list; only an explicit disable makes traffic really go through the proxy
            launch_args.append("--proxy-bypass-list=<-loopback>")
        self.ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=not self.headed,
            proxy=proxy,
            viewport=self.cfg["viewport"],
            locale=self.cfg["locale"],
            channel=self.channel,          # e.g. "chrome": use the local real Chrome; higher verification success rate
            user_agent=self.user_agent,
            args=launch_args,
            ignore_default_args=["--enable-automation"],
        )
        self.ctx.set_default_timeout(15_000)
        self._configure_performance()
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

    def _configure_performance(self) -> None:
        """Limit cosmetic work and optional tracking scripts on Arena pages only."""
        assert self.ctx is not None
        if self.cfg.get("reduce_visual_effects", True):
            self.ctx.add_init_script(script="""(() => {
                if (!(location.hostname === 'arena.ai' || location.hostname.endsWith('.arena.ai'))) return;
                const apply = () => { if (!document.head || document.getElementById('arena-cli-reduced-effects')) return;
                  const style = document.createElement('style'); style.id = 'arena-cli-reduced-effects';
                  style.textContent = '*, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; backdrop-filter: none !important; }';
                  document.head.appendChild(style); };
                if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply, {once:true}); else apply();
            })();""")
        if self.cfg.get("block_analytics_scripts", True):
            self.ctx.add_init_script(script="""(() => {
                if (!(location.hostname === 'arena.ai' || location.hostname.endsWith('.arena.ai'))) return;
                window.dataLayer = window.dataLayer || []; window.gtag = window.gtag || function() {};
                window.ga = window.ga || function() {}; window.fbq = window.fbq || function() {};
                window.analytics = window.analytics || {track: function() {}, page: function() {}, identify: function() {}};
            })();""")
            def route_script(route: Any) -> None:
                from urllib.parse import urlsplit
                request = route.request
                try:
                    source = (urlsplit(request.frame.url).hostname or "").lower()
                except PWError:
                    route.fallback()
                    return
                if source != "arena.ai" and not source.endswith(".arena.ai"):
                    route.fallback()
                    return
                u = urlsplit(request.url)
                host = (u.hostname or "").lower()
                arena = host == "arena.ai" or host.endswith(".arena.ai")
                tracking = (host == "google-analytics.com" or host.endswith(".google-analytics.com") or
                            host in ("analytics.google.com", "www.googletagmanager.com", "www.googleadservices.com", "stats.g.doubleclick.net"))
                if request.resource_type == "script" and (tracking or (arena and u.path.startswith("/rpc"))):
                    route.abort()
                else:
                    route.fallback()
            self.ctx.route("**/*", route_script)

    # ---- cookies / localStorage persistence ---------------------------------------
    def _restore_state(self) -> None:
        """Inject the cookies + localStorage from state.json into a new browser context."""
        if not self.state_file.exists():
            return
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"State file is corrupted; ignored: {self.state_file}")
            return

        from urllib.parse import urlsplit
        from .url_safety import origin
        host = (urlsplit(self.cfg["base_url"]).hostname or "").lower()
        cookies = state if isinstance(state, list) else state.get("cookies") or []
        cookies = [c for c in cookies if isinstance(c, dict) and isinstance(c.get("domain"), str)
                   and (host == c["domain"].lstrip(".").lower() or host.endswith("." + c["domain"].lstrip(".").lower()))]
        if cookies:
            try:
                self.ctx.add_cookies(cookies)
                log(f"Injected {len(cookies)} cookies")
            except PWError:
                log("Failed to inject cookies; re-import a valid cookies file")
        origins = state.get("origins") or [] if isinstance(state, dict) else []
        allowed = []
        for item in origins:
            try:
                if origin(item["origin"]) == origin(self.cfg["base_url"]):
                    allowed.append(item)
            except (ValueError, KeyError, TypeError):
                continue
        if allowed:
            blob = {o["origin"]: {kv["name"]: kv["value"] for kv in o.get("localStorage", [])} for o in allowed}
            js = "(() => { const S = %s; const o = S[location.origin]; if (o) { for (const k in o) "
            js += "{ try { localStorage.setItem(k, o[k]) } catch (e) {} } } })();"
            self.ctx.add_init_script(script=js % json.dumps(blob))

    def save_state(self) -> None:
        """Write the current cookies (+ localStorage) back to state.json — this is what \"remembers the cookies\"."""
        if not self.ctx:
            return
        try:
            state = self.ctx.storage_state()
        except PWError as exc:
            log(f"Failed to save session: {exc}")
            return
        from .storage import write_private_text
        write_private_text(self.state_file, json.dumps(state, ensure_ascii=False, indent=2))
        log(f"Session saved: {self.state_file} ({len(state.get('cookies', []))} cookies)")

    # ---- Page navigation ------------------------------------------------------------
    def _same_origin_url(self, target: str) -> str:
        from .url_safety import same_origin_url
        try:
            return same_origin_url(self.cfg["base_url"], target)
        except ValueError:
            raise SkillError("BAD_INPUT", "URL must use the configured site's origin") from None

    def _check_page_origin(self) -> None:
        assert self.page is not None
        self._same_origin_url(self.page.url)

    def _navigate(self, url: str) -> None:
        """Block cross-origin top-level redirects before the destination request."""
        assert self.page is not None and self.ctx is not None
        cdp = None
        blocked = False
        try:
            cdp = self.ctx.new_cdp_session(self.page)
            main_frame = cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
            def on_request(event: dict[str, Any]) -> None:
                nonlocal blocked
                try:
                    if event.get("frameId") == main_frame:
                        self._same_origin_url(event["request"]["url"])
                except SkillError:
                    blocked = True
                    cdp.send("Fetch.failRequest", {"requestId": event["requestId"], "errorReason": "BlockedByClient"})
                else:
                    cdp.send("Fetch.continueRequest", {"requestId": event["requestId"]})
            cdp.on("Fetch.requestPaused", on_request)
            cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}]})
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except PWTimeout:
                log("Page load timed out; checking whether the page is usable")
            except PWError as exc:
                if blocked:
                    raise SkillError("BAD_INPUT", "The site redirected outside the configured origin") from None
                raise SkillError("BROWSER_ERROR", f"Cannot open the configured site: {str(exc).splitlines()[0]}") from None
            if blocked:
                raise SkillError("BAD_INPUT", "The site redirected outside the configured origin")
            self._check_page_origin()
        except PWError as exc:
            raise SkillError("BROWSER_ERROR", f"Cannot verify navigation origin: {str(exc).splitlines()[0]}") from None
        finally:
            if cdp is not None:
                try:
                    cdp.detach()
                except PWError:
                    pass

    def open_agent(self) -> Page:
        assert self.page is not None
        url = self._same_origin_url(self.cfg["base_url"].rstrip("/") + self.cfg["agent_path"])
        log(f"Opening {url}")
        self._navigate(url)
        self.wait_editor()
        self.handle_terms()
        self.settle()
        return self.page

    def settle(self, timeout_ms: int = 2_000) -> None:
        """Wait for the SPA to fully hydrate.

        Pitfall: after `domcontentloaded` the editor is already in the DOM, but React has not attached its event handlers yet;
        clicking send at that point does nothing — the message is not submitted, the text stays in the box, and a pile of page UI text is extracted at the end.
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
        """Return the matched "human verification" marker text (None if absent)."""
        assert self.page is not None
        txt = body_text(self.page)
        for marker in self.cfg["challenge_markers"]:
            if marker.lower() in txt.lower():
                return marker
        return None

    def try_pass_challenge(self) -> bool:
        """Best effort to pass human verification: click the Turnstile checkbox once, then wait for it to move on by itself.

        Note: Cloudflare managed challenges are usually "invisible" (they pass automatically when the browser fingerprint is acceptable);
        auto-clicking is only a fallback. If you are judged to be a bot, the correct approach is to complete it manually once with --headed,
        or import the cookies from a real user's browser (see import-cookies).
        """
        marker = self.challenge_marker()
        if not marker:
            return False
        if time.time() - self._challenge_logged_at > 15:
            self._challenge_logged_at = time.time()
            log(f"Human verification page detected ({marker}); trying to pass…")
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
                    log("Clicked the verification checkbox")
            except PWError:
                continue
        self.page.wait_for_timeout(3000)
        return True

    def open_chat(self, chat: str, require_editor: bool = True) -> Page:
        """Open a specific conversation (accepts a full URL, /agent/<id>, or a bare id).

        require_editor=False: it is enough that the page is accessible (viewing workspace files does not need the input box).
        """
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
        url = self._same_origin_url(url)
        log(f"Opening conversation {url}")
        self._navigate(url)
        if require_editor:
            self.wait_editor()
        else:
            self.wait_loaded()
        self.handle_terms()
        return self.page

    def wait_loaded(self, timeout_s: float = 30) -> None:
        """Used when no composer is needed: wait for the page body to render (handling human verification along the way)."""
        assert self.page is not None
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if len(body_text(self.page)) > 40:
                    return
            except PWError:
                pass
            if self.try_pass_challenge():
                continue
            self.page.wait_for_timeout(700)

    def wait_editor(self, timeout_s: float = 60) -> Locator:
        """Wait for the input box to appear; automatically handles the terms dialog and human-verification pages along the way."""
        assert self.page is not None
        started = time.time()
        deadline = started + timeout_s
        saw_challenge = False
        while time.time() < deadline:
            self._check_page_origin()
            loc = first_visible(self.page, self.cfg["editor_selectors"])
            if loc is not None:
                return loc
            if self.dismiss_feedback():       # Conversation page: the feedback card is blocking the input box
                continue
            if self.try_pass_challenge():
                saw_challenge = True
                continue
            self.page.wait_for_timeout(700)
        if saw_challenge:
            raise SkillError(
                "BLOCKED",
                "Blocked by human verification (Cloudflare). Suggestions: 1) open a window with --headed, complete the check manually, then rerun; "
                "2) or export cookies from the browser you normally use and import them with import-cookies; "
                "3) change networks / reduce call frequency and try again",
            )
        raise SkillError(
            "UI_CHANGED",
            f"No input box (composer) found within {timeout_s:.0f}s. The page may have changed, been blocked, or require login, "
            "or the \"Was this task successful?\" feedback card was not dismissed; "
            "use --headed to observe, and check editor_selectors in config.json",
        )

    # ---- Dialog handling ------------------------------------------------------------
    def dismiss_feedback(self) -> bool:
        """Dismiss the \"Was this task successful?\" feedback card (it occupies the input box's position)."""
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
                    log("Closed the feedback card (clicked the close button)")
                    page.wait_for_timeout(300)
                    return True
            except PWError:
                continue
        try:                                  # The card says Esc; pressing the key directly is the most reliable
            page.click("body", position={"x": 5, "y": 5}, timeout=1000)
            page.keyboard.press("Escape")
            log("Closed the feedback card (Esc)")
            page.wait_for_timeout(300)
            return True
        except PWError:
            return False

    def handle_terms(self, quiet: bool = True) -> bool:
        """On first visit a Terms of Use dialog appears; you must click Agree."""
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
                log(f"Agreed to the terms ({hit})")
            page.wait_for_timeout(800)
            return True
        return False

    def login_dialog_visible(self) -> bool:
        assert self.page is not None
        page = self.page
        try:
            dlg = page.locator(f'[role="dialog"]:has-text("{self.cfg["login_dialog_text"]}")')
            for i in range(dlg.count()):
                if dlg.nth(i).is_visible():   # Only count dialogs that are actually visible
                    return True
        except PWError:
            pass
        txt = body_text(page)
        return any(t in txt for t in ("Continue with Google", "Continue with email"))

    # ---- Conversation workspace (Workspace panel) ---------------------------------------
    def open_workspace(self, chat: Optional[str] = None) -> Page:
        """Open the conversation page (or landing page) and wait for the Workspace panel to appear."""
        assert self.page is not None
        if chat:
            self.open_chat(chat, require_editor=False)
        else:
            self.open_agent()
        if chat:
            self.settle()
        deadline = time.time() + 25
        while time.time() < deadline:
            if self.workspace_raw().get("found"):
                return self.page
            self.dismiss_feedback()
            self.page.wait_for_timeout(600)
        raise SkillError(
            "UI_CHANGED",
            "Workspace panel (right-side file list) not found. The page may have changed, or this conversation has no workspace; "
            "try arena chat open <chat> first, or observe with --headed",
        )

    def workspace_raw(self) -> dict[str, Any]:
        assert self.page is not None
        try:
            return self.page.evaluate(WS_PANEL_JS, self.cfg.get("workspace_panel_selectors") or [])
        except PWError:
            return {"found": False, "stats": [], "entries": [], "expandable": 0}

    def expand_workspace(self, limit: Optional[int] = None) -> int:
        """Click each collapsed folder to expand it (child items are not in the DOM, so they cannot be listed unless expanded)."""
        assert self.page is not None
        limit = int(limit if limit is not None else self.cfg.get("workspace_expand_clicks", 60))
        clicks = 0
        while clicks < limit:
            try:
                if not self.page.evaluate(WS_EXPAND_JS, self.cfg.get("workspace_panel_selectors") or []):
                    break
            except PWError:
                break
            clicks += 1
            self.page.wait_for_timeout(250)
        if clicks:
            log(f"Expanded {clicks} folder(s)")
        return clicks

    def workspace_entries(self, chat: Optional[str] = None,
                          expand: bool = True) -> dict[str, Any]:
        """Workspace listing for the conversation: {stats, entries (with path), archive_href, chat}."""
        from .workspace import build_paths, parse_stats

        self.open_workspace(chat)
        if expand:
            self.expand_workspace()
        raw = self.workspace_raw()
        entries = build_paths(raw.get("entries") or [])
        files = [e for e in entries if e.get("kind") == "file"]
        stats = parse_stats(raw.get("stats") or [])
        source = "dom"
        # The panel clearly has files but no rows could be read → switch to another UI variant: list from the "Download workspace" zip directly
        if not files and (stats.get("files") or 0) > 0:
            try:
                zip_entries = self.workspace_zip_entries(None)
            except SkillError:
                zip_entries = []
            if zip_entries:
                entries, files, source = zip_entries, zip_entries, "archive"
        # Also add a size field (present in the zip; absent from the DOM)
        sizes = {e["path"]: e.get("size") for e in entries if e.get("size") is not None}
        for f in files:
            f.setdefault("size", sizes.get(f["path"]))
        return {
            "chat_id": extract_chat_id(self.page.url if self.page else ""),
            "stats": stats,
            "entries": entries,
            "count": len(files),
            "archive_href": raw.get("archive_href") or self._archive_href(),
            "source": source,
            "truncated": bool(raw.get("expandable")),
        }

    def workspace_bytes(self, href: str) -> tuple[bytes, dict[str, str]]:
        """Fetch the files via the browser context (carrying the login session)."""
        assert self.ctx is not None
        from urllib.parse import urljoin
        url = self._same_origin_url(href)
        for _ in range(6):
            resp = self.ctx.request.get(url, timeout=120_000, max_redirects=0)
            if resp.status not in (301, 302, 303, 307, 308):
                break
            location = resp.headers.get("location")
            if not location:
                raise SkillError("SEND_FAILED", "Download redirect has no destination")
            url = self._same_origin_url(urljoin(url, location))
        else:
            raise SkillError("SEND_FAILED", "Too many download redirects")
        if not resp.ok:
            raise SkillError(
                "SEND_FAILED" if resp.status >= 500 else "NOT_FOUND",
                f"Download failed: HTTP {resp.status} ({url})",
            )
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.body(), headers

    def workspace_file(self, chat: Optional[str], path: str) -> dict[str, Any]:
        """Fetch one file by path: first locate it in the (expanded) listing, then download it."""
        from .workspace import ambiguous_candidates, find_file, normalize_ref

        listing = self.workspace_entries(chat, expand=True)
        entry = find_file(listing["entries"], path)
        if entry is not None and not entry.get("href") and listing.get("source") == "archive":
            return self.workspace_zip_read(entry["path"], chat)
        if entry is None:
            cands = ambiguous_candidates(listing["entries"], path)
            if len(cands) > 1:
                raise SkillError(
                    "BAD_INPUT",
                    "Multiple files share this name; please provide the full path: " + ", ".join(cands[:8]),
                )
            raise SkillError(
                "NOT_FOUND",
                f"No {normalize_ref(path)} in the workspace; check with arena workspace list",
            )
        if not entry.get("href"):
            return self.workspace_zip_read(entry["path"], chat)
        data, headers = self.workspace_bytes(entry["href"])
        return {
            "chat_id": listing["chat_id"],
            "path": entry["path"],
            "name": entry["name"],
            "bytes": data,
            "content_type": headers.get("content-type", ""),
            "size": len(data),
        }

    def _archive_href(self) -> Optional[str]:
        if not getattr(self, "_ws_archive_href", None):
            self._ws_archive_href = self.workspace_raw().get("archive_href")
        return self._ws_archive_href

    def workspace_archive(self, chat: Optional[str] = None) -> dict[str, Any]:
        """Package the entire workspace (the zip provided by the \"Download workspace\" button at the top right of the page)."""
        if getattr(self, "_ws_zip", None) is not None:
            return self._ws_zip
        self.open_workspace(chat)
        href = self._archive_href()
        if not href:
            raise SkillError("UI_CHANGED", "Could not find the \"Download workspace\" link (the page may have changed)")
        data, headers = self.workspace_bytes(href)
        got = {"chat_id": extract_chat_id(self.page.url if self.page else ""),
               "bytes": data, "content_type": headers.get("content-type", ""),
               "size": len(data)}
        self._ws_zip = got
        return got

    def workspace_zip_entries(self, chat: Optional[str] = None) -> list[dict[str, Any]]:
        """Split the workspace zip into entries (fallback when the page DOM cannot be recognized: path/size are the most accurate)."""
        import io
        import zipfile

        got = self.workspace_archive(chat)
        try:
            zf = zipfile.ZipFile(io.BytesIO(got["bytes"]))
        except zipfile.BadZipFile:
            raise SkillError("UI_CHANGED",
                             "\"Download workspace\" did not return a zip (it may have been blocked or the page changed)") from None
        entries: list[dict[str, Any]] = []
        for info in zf.infolist():
            name = info.filename.rstrip("/")
            if not name or info.is_dir():
                continue
            entries.append({"kind": "file", "name": name.rsplit("/", 1)[-1],
                            "depth": name.count("/"), "path": name,
                            "size": info.file_size, "href": None})
        return entries

    def workspace_zip_read(self, path: str, chat: Optional[str] = None) -> dict[str, Any]:
        import io
        import zipfile

        from .workspace import normalize_ref

        ref = normalize_ref(path)
        got = self.workspace_archive(chat)
        zf = zipfile.ZipFile(io.BytesIO(got["bytes"]))
        names = [n for n in zf.namelist() if not n.endswith("/")]
        hit = next((n for n in names if n == ref), None)
        if hit is None:
            cands = [n for n in names if n.endswith("/" + ref) or n.rsplit("/", 1)[-1] == ref]
            if len(cands) == 1:
                hit = cands[0]
            elif len(cands) > 1:
                raise SkillError("BAD_INPUT", "Multiple files share this name; please provide the full path: " + ", ".join(cands[:8]))
        if hit is None:
            raise SkillError("NOT_FOUND", f"No {ref} in the workspace (zip)")
        data = zf.read(hit)
        return {"chat_id": got["chat_id"], "path": hit, "name": hit.rsplit("/", 1)[-1],
                "bytes": data, "content_type": "", "size": len(data)}

    def list_chats(self, limit: int = 20) -> list[dict[str, Any]]:
        """Read recent conversations from the sidebar (title + link)."""
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

    # ---- Input & send ---------------------------------------------------------
    def fill_editor(self, text: str) -> None:
        """Write content into Tiptap/ProseMirror: simulated paste is preferred (supports multi-line / long text); falls back to keystroke-by-keystroke input on failure."""
        assert self.page is not None
        page = self.page
        editor = self.wait_editor()
        self._check_page_origin()
        editor.click()
        jitter()

        # 1) Clear existing content
        try:
            page.keyboard.press("ControlOrMeta+a")
            page.keyboard.press("Delete")
        except PWError:
            pass

        # 2) Paste event (ProseMirror converts plain text into paragraphs)
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
                log("Wrote into the input box (paste method)")
                return
        except PWError as exc:
            log(f"Paste method failed ({exc}); switching to keyboard input")

        # 3) Keyboard input fallback; for multiple lines use Shift+Enter for line breaks to avoid triggering send
        editor.click()
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if i:
                page.keyboard.press("Shift+Enter")
            page.keyboard.insert_text(line)
            jitter(0.01, 0.05)
        log("Wrote into the input box (keyboard method)")

    def submit(self) -> None:
        """Click the send button; fall back to Enter if the button is unavailable."""
        assert self.page is not None
        page = self.page
        self._check_page_origin()
        btn = first_visible(page, self.cfg["send_selectors"])
        if btn is not None:
            try:
                if btn.is_enabled():
                    btn.click(timeout=5_000)
                    log("Clicked send")
                    return
            except PWError:
                raise SkillError("SEND_FAILED", "Could not confirm send. Check the conversation before retrying") from None
            raise SkillError("SEND_FAILED", "Send button is disabled. Check the conversation before retrying")
        editor = self.wait_editor(5)
        try:
            editor.click(timeout=5_000)
            page.keyboard.press("Enter")
        except PWError:
            raise SkillError("SEND_FAILED", "Could not confirm send. Check the conversation before retrying") from None
        log("Sent with Enter")

    # ---- State detection -----------------------------------------------------------
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
        """Translate a failed API call into an error code (429 = rate limiting, the most common case)."""
        for status, url in failures:
            if status == 429:
                raise SkillError(
                    "RATE_LIMITED",
                    f"Arena rejected the new conversation request (HTTP 429, {url}): too many requests or quota exhausted",
                )
            if status in (401, 403):
                raise SkillError(
                    "LOGIN_REQUIRED",
                    f"The server rejected the session (HTTP {status}, {url}): cookies have expired or are not a login session",
                )
        if failures:
            status, url = failures[0]
            raise SkillError("SEND_FAILED", f"Arena API returned HTTP {status} ({url}); the message was not sent")

    def has_transcript(self) -> bool:
        """Whether the conversation area has appeared on the page (indicating this is a chat page rather than a landing / empty-state page)."""
        assert self.page is not None
        for sel in (self.cfg.get("turn_selectors") or []):
            try:
                if self.page.locator(sel).count():
                    return True
            except PWError:
                continue
        return False

    # ---- Answer extraction -----------------------------------------------------------
    def assistant_counts(self) -> dict[str, int]:
        assert self.page is not None
        counts = {}
        for sel in self.cfg["assistant_selectors"]:
            try:
                counts[sel] = self.page.locator(sel).count()
            except PWError:
                counts[sel] = 0
        return counts

    def extract_answer(self, prompt: str, baseline: str, assistant_baseline: Optional[dict[str, int]] = None) -> str:
        """Three-tier strategy: structured selectors -> text diff -> full-page text."""
        assert self.page is not None
        page = self.page

        # A. Structured: take the last visible assistant container
        cands = []
        for sel in self.cfg["assistant_selectors"]:
            try:
                loc = page.locator(sel)
                for i in range((assistant_baseline or {}).get(sel, 0), loc.count()):
                    item = loc.nth(i)
                    if item.is_visible():
                        cands.append(item)
            except PWError:
                continue
        if cands:
            for cand in reversed(cands):          # Search from the last entry backward, skipping ones that are actually user messages
                try:
                    text = clean_answer(cand.inner_text(), self.cfg)
                except PWError:
                    continue
                if not text:
                    continue
                if norm(text) == norm(prompt):    # This matched the user's own message → the answer has not appeared yet
                    continue
                if text.strip().lower() in UI_TEXT_BLACKLIST:   # Control labels such as "Agent ⌄"
                    continue
                if len(text) > 1:
                    return text

        # A2. A conversation area exists but no assistant turn has appeared yet → still generating / navigating.
        #     In that case never fall back to the full-page diff (it would pick up noise such as "Agent" or sidebar titles).
        if self.has_transcript():
            return ""

        # B. Text diff: take the text that newly appeared after the user's message
        now = body_text(page)
        key = norm(prompt)[:60]
        idx = now.rfind(key)
        if idx != -1:
            tail = now[idx + len(key):]
            tail = clean_answer(tail, self.cfg)
            if len(tail) > 1:
                return tail

        # C. Fallback: newly added text on the whole page
        base = set(line.strip() for line in baseline.splitlines() if line.strip())
        new_lines = [ln.strip() for ln in now.splitlines()
                     if ln.strip() and ln.strip() not in base]
        return clean_answer("\n".join(new_lines), self.cfg)

    # ---- Main flow: ask -------------------------------------------------------
    def ask(self, prompt: str, timeout: Optional[float] = None,
            stable: Optional[float] = None, chat: Optional[str] = None,
            on_update: Optional[Any] = None,
            poll: Optional[float] = None) -> dict[str, Any]:
        """Ask a question and retrieve the answer.

        on_update(text): called each time a longer answer is extracted (used for streaming output / progress logs).
        poll: polling interval in seconds (use a smaller value when streaming for a more responsive feel).
        """
        timeout = timeout or self.cfg["wait_timeout"]
        stable = stable or self.cfg["stable_seconds"]
        poll_seconds = float(poll or self.cfg["poll_seconds"])
        assert self.page is not None
        page = self.page

        if chat:                                # Continue asking in an existing conversation
            self.open_chat(chat)
        else:
            self.open_agent()
        baseline = body_text(page)
        assistant_baseline = self.assistant_counts()
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

        # 1) Check whether a login wall is blocking (when anonymous, the message is not really sent)
        def wait_sent(seconds: float) -> bool:
            deadline_ = time.time() + seconds
            while time.time() < deadline_:
                self.raise_http_failure(http_failures)
                if self.login_dialog_visible():
                    raise SkillError(
                        "LOGIN_REQUIRED",
                        "Sending was blocked by the login dialog: the current session is not logged in or has expired; run `login` or import cookies first",
                    )
                if self.message_count() > before_msgs:
                    return True
                try:
                    text = editor.evaluate_all("els => els.length ? (els[els.length-1].tagName === 'TEXTAREA' ? els[els.length-1].value : els[els.length-1].innerText) : null")
                    if text is not None and norm(text) == "":
                        return True
                except PWError:
                    pass
                page.wait_for_timeout(400)
            return False

        if not wait_sent(20):
            raise SkillError("SEND_FAILED", "Could not confirm that the message was submitted. Check the conversation before retrying")

        log("Message submitted; waiting for the Agent to run…")

        # 2) Wait for streaming output to end (text stable + no stop button)
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
                # The message was rendered optimistically and then fully retracted by the page: the server rejected this session (this is what rate limiting looks like)
                self.raise_http_failure(http_failures or [(429, "create-chat")])
                raise SkillError("SEND_FAILED",
                                 "The message was retracted by the page after sending (server rejection); try again later")
            answer = self.extract_answer(prompt, baseline, assistant_baseline)
            if answer != last_text:
                last_text, last_change = answer, time.time()
                if on_update is not None and answer:
                    try:
                        on_update(answer)
                    except Exception:  # noqa: BLE001  errors in the callback must not affect the main flow
                        pass
            else:
                if answer and not self.is_streaming() and time.time() - last_change >= stable:
                    log(f"Output stabilized ({time.time() - started:.0f}s)")
                    break
            for marker in self.cfg["error_markers"]:
                if marker.lower() in answer.lower():
                    log(f"Page error marker: {marker}")
        else:
            raise SkillError("TIMEOUT", f"Still no complete answer after waiting {timeout:.0f}s")

        answer = clean_answer(answer or last_text, self.cfg)
        if not answer:
            raise SkillError("UI_CHANGED", "No answer text was extracted; check the assistant_selectors configuration")

        # 3) Close any leftover dialogs and save the session
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
            "reply_markdown": answer,  # The upper layer can forward this directly to the user
        }


def extract_chat_id(url: str) -> Optional[str]:
    m = re.search(r"/agent/(?:c/)?([0-9a-zA-Z-]{8,})", url)
    return m.group(1) if m else None


# --------------------------------------------------------------------------------------
# Workspace panel: locate it + read entries + expand folders one by one (all run inside the page; no API guessing)
# --------------------------------------------------------------------------------------
WS_PANEL_JS = r"""
(customSelectors) => {
  const q = (root, sel) => Array.from(root.querySelectorAll(sel));
  let panel = null;
  for (const sel of (customSelectors || [])) {
    const el = document.querySelector(sel);
    if (el) { panel = el; break; }
  }
  if (!panel) {
    // Walk up from the "Download workspace" link: first look for the container that **also contains file rows** (the real panel root),
    // if none is found (empty workspace), fall back to a container matched only by the "Workspace" + "files" text.
    const anchor = document.querySelector('a[aria-label="Download workspace"]');
    // Note: do not include a[aria-label^="Download "] — the "Download workspace" button in the header
    // is also inside the header and would cause the header to be mistaken for the panel root.
    const hasRows = (n) => !!(n.querySelector && n.querySelector(
      '[aria-label$=" file"], [aria-label$=" folder"]'));
    for (const needRows of [true, false]) {
      let node = anchor;
      while (node && node !== document.body) {
        const t = node.innerText || '';
        if (/Workspace/.test(t) && /\bfiles\b/i.test(t) && (!needRows || hasRows(node))) {
          panel = node;
          break;
        }
        node = node.parentElement;
      }
      if (panel) break;
    }
  }
  if (!panel) {
    // Fallback: find the card that contains the "Workspace" heading
    const title = q(document, 'span,div,div[class]').find(e => (e.textContent || '').trim() === 'Workspace');
    if (title) {
      let node = title;
      while (node && node !== document.body) {
        if ((node.innerText || '').includes('files')) { panel = node; break; }
        node = node.parentElement;
      }
    }
  }
  if (!panel) return {found: false, stats: [], entries: [], expandable: 0};

  const stats = (panel.innerText || '').split('\n').map(s => s.trim()).filter(Boolean).slice(0, 6);
  const entries = [];
  for (const el of q(panel, 'div[role="button"][aria-label$=" file"], button[aria-label$=" folder"], div[role="button"][aria-label$=" folder"]')) {
    const label = el.getAttribute('aria-label') || '';
    const m = (el.getAttribute('style') || '').match(/padding-left:\s*(\d+)px/);
    const depth = m ? Math.max(0, Math.round((parseInt(m[1], 10) - 8) / 16)) : 0;
    const folder = label.match(/^(?:Collapse|Expand) (.*) folder$/);
    if (folder) {
      entries.push({kind: 'folder', name: folder[1], depth,
                    expanded: label.startsWith('Collapse')});
      continue;
    }
    const file = label.match(/^(.*) file$/);
    if (file) {
      const a = el.querySelector('a[download], a[aria-label^="Download "]');
      entries.push({kind: 'file', name: file[1], depth,
                    href: a ? a.getAttribute('href') : null,
                    download: a ? a.getAttribute('download') : null});
    }
  }
  const wsDl = panel.querySelector('a[aria-label="Download workspace"]');
  return {found: true, stats, entries,
          expandable: q(panel, 'button[aria-label^="Expand "], div[role="button"][aria-label^="Expand "]').length,
          archive_href: wsDl ? wsDl.getAttribute('href') : null};
}
"""

WS_EXPAND_JS = r"""
(customSelectors) => {
  let panel = null;
  for (const sel of (customSelectors || [])) {
    const el = document.querySelector(sel);
    if (el) { panel = el; break; }
  }
  if (!panel) {
    // Walk up from the "Download workspace" link: first look for the container that **also contains file rows** (the real panel root),
    // if none is found (empty workspace), fall back to a container matched only by the "Workspace" + "files" text.
    const anchor = document.querySelector('a[aria-label="Download workspace"]');
    // Note: do not include a[aria-label^="Download "] — the "Download workspace" button in the header
    // is also inside the header and would cause the header to be mistaken for the panel root.
    const hasRows = (n) => !!(n.querySelector && n.querySelector(
      '[aria-label$=" file"], [aria-label$=" folder"]'));
    for (const needRows of [true, false]) {
      let node = anchor;
      while (node && node !== document.body) {
        const t = node.innerText || '';
        if (/Workspace/.test(t) && /\bfiles\b/i.test(t) && (!needRows || hasRows(node))) {
          panel = node;
          break;
        }
        node = node.parentElement;
      }
      if (panel) break;
    }
  }
  if (!panel) return false;
  const btn = panel.querySelector('button[aria-label^="Expand "], div[role="button"][aria-label^="Expand "]');
  if (!btn) return false;
  btn.click();
  return true;
}
"""


# These texts appear on the page but are never answers (control / placeholder text of the chat page)
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
    # Cut off UI blocks that leaked into the page (feedback bar, Workspace panel, Connectors list, etc.)
    for marker in cfg.get("answer_stop_markers", []):
        idx = out.find(marker)
        if idx > 0:
            out = out[:idx]
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


# --------------------------------------------------------------------------------------
# Error types
# --------------------------------------------------------------------------------------
class SkillError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# Rejections from these endpoints (4xx/5xx) essentially mean "this message was not sent"
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
# Command implementations
# --------------------------------------------------------------------------------------
def cmd_check(session: Session, probe: bool = False,
              probe_text: str = "hi") -> dict[str, Any]:
    """Determine whether the session is usable.

    Three possible results:
      logged_in = True   found an account entry that only appears after login, and no login dialog
      logged_in = False  a login dialog is visible (definitely not logged in)
      logged_in = None   cannot tell (--probe is recommended: actually send one message to see whether a login wall blocks it)
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
        return {"ok": True, "logged_in": False, "hint": "Definitely not logged in: run `login` or import cookies", **base}

    if probe:
        editor = session.wait_editor()
        session.fill_editor(probe_text)
        session.submit()
        deadline = time.time() + 15
        while time.time() < deadline:
            if session.login_dialog_visible():
                return {"ok": True, "logged_in": False, "probe": True,
                        "hint": "Sending was blocked by the login wall; please log in again", **base}
            try:
                if norm(editor.inner_text()) == "":
                    return {"ok": True, "logged_in": True, "probe": True,
                            "hint": "Probe message sent (this creates a chat record)", **base}
            except PWError:
                break
            page.wait_for_timeout(500)
        return {"ok": True, "logged_in": None, "probe": True,
                "hint": "The probe result is inconclusive; test with `ask`", **base}

    txt = body_text(page)
    anon = session.cfg.get("anonymous_markers") or []
    if any(m in txt for m in anon):
        return {"ok": True, "logged_in": False,
                "hint": "The page is the logged-out landing page (cookies are missing or expired); please re-import cookies", **base}

    if first_visible(page, session.cfg.get("logged_in_selectors") or []) is not None:
        return {"ok": True, "logged_in": True,
                "hint": "Conversation history sidebar detected; the session is valid", **base}

    if first_visible(page, session.cfg["account_selectors"]) is not None:
        return {"ok": True, "logged_in": True, "hint": "Account entry detected; the session looks valid", **base}

    return {"ok": True, "logged_in": None,
            "hint": "Cannot confirm the login state; --probe sends a real message to verify it (this leaves a chat record)", **base}


def cmd_login(session: Session, email: Optional[str], code: Optional[str],
              wait_s: float, probe_text: str) -> dict[str, Any]:
    """Trigger and complete login, then save the session.

    Flow (verified login path on arena.ai):
      Open /agent -> on first visit a Terms dialog appears (click Agree) -> clicking send brings up "Log In or Create Account"
      -> enter email -> Continue with email -> click the magic link in the email (or enter the code) -> logged in
    """
    page = session.open_agent()

    if not session.login_dialog_visible():
        # Trigger the login dialog by "sending a probe message"
        session.fill_editor(probe_text)
        session.submit()
        deadline = time.time() + 15
        while time.time() < deadline and not session.login_dialog_visible():
            page.wait_for_timeout(500)
        session.handle_terms()

    if not session.login_dialog_visible():
        # No login dialog appeared: may already be logged in
        session.save_state()
        return {"ok": True, "already_logged_in": True, "state_file": str(session.state_file)}

    if email:
        field = first_visible(page, session.cfg["email_input_selectors"])
        if field is None:
            raise SkillError("UI_CHANGED", "Could not find the email input in the login dialog")
        field.click()
        field.fill(email)
        jitter()
        hit = click_text(page, session.cfg["continue_email_texts"])
        log(f"Email submitted ({hit or 'button not matched'}); check your inbox for the login link"
            + (", or pass the verification code with --code" if not code else ""))
        page.wait_for_timeout(2500)
        err = body_text(page)
        for marker in ("not permitted", "invalid", "Enter a valid"):
            if marker.lower() in err.lower():
                raise SkillError("LOGIN_FAILED", f"Email rejected: {marker}")

    if code:
        field = first_visible(page, session.cfg["code_input_selectors"])
        if field is None:
            raise SkillError("UI_CHANGED", "Could not find the verification code input")
        field.click()
        page.keyboard.insert_text(code)
        page.wait_for_timeout(500)
        click_text(page, ["Verify", "Continue", "Submit", "Sign in"])
        log("Verification code submitted")
    else:
        log(f"Waiting for login to complete (up to {wait_s:.0f}s): open the email link or enter the code in the same browser window")

    # Poll until login succeeds (login dialog gone and input box available)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        page.wait_for_timeout(1000)
        if not session.login_dialog_visible() and first_visible(page, session.cfg["editor_selectors"]):
            session.save_state()
            # Clear the probe message to avoid leaving junk conversations behind
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
    raise SkillError("LOGIN_TIMEOUT", f"Login completion not detected within {wait_s:.0f}s")


def cmd_export(session: Session, out: Path) -> dict[str, Any]:
    assert session.ctx is not None
    state = session.ctx.storage_state()
    from .storage import write_private_text
    write_private_text(out, json.dumps(state, ensure_ascii=False, indent=2))
    return {"ok": True, "exported": str(out), "cookies": len(state.get("cookies", []))}


SAME_SITE = {"lax": "Lax", "strict": "Strict", "none": "None", "no_restriction": "None"}
NETSCAPE_HINT = "Netscape HTTP Cookie File"


def _norm_cookie(c: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Normalize into a shape Playwright add_cookies() accepts; also handle compatibility for several common formats along the way."""
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
    if expires != -1 and expires < 1_000_000_000:      # Some extensions provide milliseconds
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
    """Parse a cookies file, automatically detecting three common formats:
      1. Netscape cookies.txt (exported by curl / browser extensions; tab-separated; may have a #HttpOnly_ prefix)
      2. JSON array from EditThisCookie / Cookie-Editor
      3. Playwright storage_state ({"cookies": [...], "origins": [...]})
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SkillError("BAD_INPUT", f"Could not read the cookies file: {exc}") from exc
    head = text.lstrip()[:200]

    # --- JSON ---
    if head.startswith(("{", "[")):
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SkillError("BAD_INPUT", f"Failed to parse cookies JSON: {exc}") from exc
        if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
            cookies = [c for c in (_norm_cookie(x) for x in raw["cookies"]) if c]
            return cookies, {"format": "storage_state", "origins": raw.get("origins") or []}
        if isinstance(raw, list):
            cookies = [c for c in (_norm_cookie(x) for x in raw) if c]
            return cookies, {"format": "json_array", "origins": []}
        raise SkillError("BAD_INPUT", "Invalid JSON format: neither a cookies array nor a cookies field")

    # --- Netscape cookies.txt ---
    cookies: list[dict[str, Any]] = []
    skipped = 0
    for line in text.splitlines():
        raw = line.rstrip("\r\n")
        if not raw.strip():
            continue
        http_only = raw.startswith("#HttpOnly_")
        if raw.startswith("#") and not http_only:
            continue                      # Comment line (including the standard file header)
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
            "This does not look like a cookies file. Supported formats: Netscape cookies.txt (curl / extension export), "
            "EditThisCookie JSON array, Playwright storage_state",
        )
    return cookies, {"format": "netscape", "skipped": skipped,
                     "had_header": NETSCAPE_HINT in text}


def summarize_cookies(cookies: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    """Cookie health check: how many cookies, which are login credentials, and whether any have expired. Only counts; never reveals values."""
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
    """Turn an uploaded cookies file into a usable session snapshot."""
    cookies, meta = parse_cookie_file(path)
    state = {"cookies": cookies, "origins": meta.get("origins") or []}
    from .storage import write_private_text
    write_private_text(state_file, json.dumps(state, ensure_ascii=False, indent=2))

    health = summarize_cookies(cookies, cfg or DEFAULT_CONFIG)
    result: dict[str, Any] = {
        "ok": True,
        "source": str(path),
        "format": meta.get("format"),
        "state_file": str(state_file),
        **health,
    }
    if not health["has_auth_cookie"]:
        result["warning"] = ("No cookie resembling a login credential was found (adjust config.auth_cookie_patterns); "
                             "if ask reports LOGIN_REQUIRED, re-export the cookies")
    if health["expired_names"]:
        result.setdefault("warning", "")
        result["warning"] += ("; some cookies have expired: " + ", ".join(health["expired_names"]))
    return result


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def _add_common_args(p: argparse.ArgumentParser) -> None:
    """Global options. SUPPRESS is used as the default so they work both before and after the subcommand."""
    S = argparse.SUPPRESS
    p.add_argument("--config", default=S, help="Configuration file path (JSON; overrides the default selectors)")
    p.add_argument("--base-url", default=S, help="Site root URL; default https://arena.ai")
    p.add_argument("--profile-dir", default=S, help="Browser user-data directory (default ~/.arena-cli/prof)")
    p.add_argument("--state-file", default=S, help="Session snapshot file (default ~/.arena-cli/state.json)")
    p.add_argument("--cookies", default=S,
                   help="Use a cookies file directly (Netscape cookies.txt / JSON): import it first, then run the command")
    p.add_argument("--headed", action="store_true", default=S, help="Show the browser window (for solving verification / debugging)")
    p.add_argument("--channel", default=S, help='Browser channel, e.g. "chrome" (uses the locally installed real Chrome)')
    p.add_argument("--user-agent", default=S, help="Override the User-Agent")
    p.add_argument("--timeout", type=float, default=S, help="Timeout in seconds for waiting for the answer (default 900)")
    p.add_argument("--stable", type=float, default=S, help="Seconds of unchanged text before generation is considered finished (default 2.5)")
    p.add_argument("--format", choices=["json", "text", "markdown"], default=S,
                   help="Output format (default json)")
    p.add_argument("--out", default=S, help="Also write the result to a file")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_args(common)

    p = argparse.ArgumentParser(
        prog="arena_agent",
        description="Drive arena.ai/agent through a browser: log in and remember cookies, send messages, and retrieve Agent results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    _add_common_args(p)

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", parents=[common], help="Check whether the session is still valid")

    ck_parser = sub.choices["check"]
    ck_parser.add_argument("--probe", action="store_true",
                           help="Send a real probe message to confirm the login state (leaves one record in the conversation)")
    ck_parser.add_argument("--probe-text", default="hi")

    a = sub.add_parser("ask", parents=[common], help="Send one message and retrieve the answer")
    a.add_argument("prompt", nargs="?", help="Content to send to the Agent; pass - to read from stdin")
    a.add_argument("--file", help="Read the prompt from a file")

    lg = sub.add_parser("login", parents=[common], help="Log in and save cookies")
    lg.add_argument("--email", help="Email address that receives the login link / verification code")
    lg.add_argument("--code", help="One-time verification code (if the page requires it)")
    lg.add_argument("--wait", type=float, default=240, help="Seconds to wait for manual login completion")
    lg.add_argument("--probe-text", default="hi", help="Placeholder message used to trigger the login dialog")

    e = sub.add_parser("export-cookies", parents=[common], help="Export current session cookies")
    e.add_argument("--cookies-out", default=None,
                   help="Export path (default ~/.arena-cli/cookies.json; do not mix with --out)")

    i = sub.add_parser("import-cookies", parents=[common], help="Import cookies JSON exported from a browser")
    i.add_argument("path", help="cookies JSON file (EditThisCookie / storage_state format)")
    return p


def emit(result: dict[str, Any], fmt: str, out: Optional[str]) -> None:
    if fmt == "json":
        payload = json.dumps(result, ensure_ascii=False, indent=2)
    elif fmt == "text":
        payload = result.get("answer") or result.get("error") or json.dumps(result, ensure_ascii=False)
    else:  # markdown: can be forwarded to the user directly
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
        # User uploaded a cookies file: import it first, then run the remaining commands as usual
        imported = cmd_import(Path(g("cookies")).expanduser(), state_file, cfg)
        log(f"Imported {imported['count']} cookies from {imported['source']}"
            f"(login credentials: {', '.join(imported['auth_cookie_names']) or 'not recognized'})")
        if imported.get("warning"):
            log(f"Note: {imported['warning']}")

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
                    raise SkillError("BAD_INPUT", "prompt is empty (usage: ask \"content\" / ask --file f.md / echo x | ask -)")
                res = session.ask(prompt, timeout=g("timeout"), stable=g("stable"))
                emit(res, fmt, out)
        return 0
    except SkillError as exc:
        emit({"ok": False, "code": exc.code, "error": exc.message}, fmt, out)
        return EXIT_CODES.get(exc.code, 1)
    except KeyboardInterrupt:
        emit({"ok": False, "code": "INTERRUPTED", "error": "User interrupted"}, fmt, out)
        return 130
    except PWError as exc:
        emit({"ok": False, "code": "BROWSER_ERROR", "error": str(exc).splitlines()[0]}, fmt, out)
        return 1


if __name__ == "__main__":
    sys.exit(main())
