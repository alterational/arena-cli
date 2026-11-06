"""Opt-in full-screen chat interface; tabs and drafts exist only in memory."""
from __future__ import annotations
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from ..errors import ArenaError


@dataclass
class Tab:
    title: str
    chat: str | None = None
    messages: list[tuple[str, str]] = field(default_factory=list)
    draft: str = ""
    cursor: int = 0
    scroll: int = 0
    uncertain: bool = False


class ChatState:
    def __init__(self, chat=None):
        self.tabs = [Tab("Chat 1", chat)]
        self.active, self.next_number = 0, 2
        self.busy = None
        self.status = "Enter adds a line · Ctrl-S sends · /help for commands"
        self.confirm_quit = False

    @property
    def tab(self):
        return self.tabs[self.active]

    def new_tab(self, chat=None):
        self.tabs.append(Tab(f"Chat {self.next_number}", chat))
        self.next_number += 1
        self.active = len(self.tabs) - 1
        self.status = "Existing chat selected; earlier messages are not loaded." if chat else "New chat"

    def switch(self, step):
        self.active = (self.active + step) % len(self.tabs)

    def close_tab(self):
        if self.tab is self.busy:
            self.status = "Cannot close the tab while its request is running."
        elif len(self.tabs) == 1:
            self.status = "At least one tab must remain open. Use /quit to exit."
        else:
            self.tabs.pop(self.active)
            self.active = min(self.active, len(self.tabs) - 1)
            self.status = "Tab closed; its Arena conversation was not deleted."

    def command(self, text):
        command, _, argument = text.strip().partition(" ")
        command, argument = command.lower(), argument.strip()
        if command not in ("/quit", "/exit"):
            self.confirm_quit = False
        if command == "/new": self.new_tab()
        elif command == "/open":
            if argument: self.new_tab(argument)
            else: self.status = "Usage: /open <chat ID, URL, last, or history index>"
        elif command == "/next": self.switch(1)
        elif command == "/prev": self.switch(-1)
        elif command == "/close": self.close_tab()
        elif command == "/help":
            self.tab.messages.append(("Help", "Ctrl-S sends; Enter adds a line. Page Up/Down scroll.\n"
                "/new creates a tab; /open REF opens an existing chat in a new tab.\n"
                "/next and /prev switch tabs; /close closes a tab; /quit exits.\n"
                "Use // to send a prompt starting with /."))
            self.tab.scroll = 0
            self.status = "Help shown in the transcript. Tabs and drafts are temporary."
        elif command in ("/quit", "/exit"): return "quit"
        else: self.status = "Unknown command. Use /help, or // to send a leading slash."
        return None

    def begin(self, prompt):
        self.confirm_quit = False
        if self.busy:
            self.status = "Wait for the current request to finish before sending another."
            return False
        if self.tab.uncertain:
            self.status = "The conversation could not be identified. Check Arena before using /open or /new."
            return False
        tab = self.tab
        first_prompt = not any(role == "You" for role, _ in tab.messages)
        tab.messages.extend((("You", prompt), ("Arena", "Waiting for a response…")))
        if tab.title.startswith("Chat ") and first_prompt:
            tab.title = " ".join(prompt.split())[:20] or tab.title
        tab.scroll, self.busy = 0, tab
        self.status = "Sending; switch tabs while the response is running."
        return True

    def event(self, event):
        tab = self.busy
        if tab is None: return
        kind = event.get("type")
        if kind == "resolved" and event.get("chat"):
            tab.chat = event["chat"]
        elif kind == "progress":
            tab.messages[-1] = ("Arena", str(event.get("text", "")))
        elif kind == "result":
            tab.messages[-1] = ("Arena", str(event.get("answer", "")))
            if event.get("chat_id"): tab.chat = str(event["chat_id"])
            elif not tab.chat: tab.uncertain = True
            self.busy, self.confirm_quit = None, False
            self.status = "Response complete." if not tab.uncertain else "Response complete, but no chat ID was found. Check Arena before continuing."
        elif kind == "error":
            if event.get("chat_url"): tab.chat = str(event["chat_url"])
            elif event.get("attempted") and not tab.chat: tab.uncertain = True
            failure = ("Error", str(event.get("message") or "The worker stopped unexpectedly.") +
                       "\nCheck the conversation before retrying; the message may have been sent.")
            if tab.messages[-1][1] == "Waiting for a response…": tab.messages[-1] = failure
            else: tab.messages.append(failure)
            self.busy, self.confirm_quit = None, False
            self.status = "Request failed; no automatic retry."


class Worker:
    def __init__(self, request):
        self.events = queue.Queue()
        env = os.environ.copy()
        root = str(Path(__file__).resolve().parent.parent.parent)
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        self.proc = subprocess.Popen([sys.executable, "-m", "arena.tui_worker"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env, start_new_session=True)
        try:
            self.proc.stdin.write(json.dumps(request, ensure_ascii=True) + "\n")
            self.proc.stdin.close()
        except Exception:
            self.stop()
            raise
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        terminal = False
        try:
            for line in self.proc.stdout:
                try: event = json.loads(line)
                except (ValueError, TypeError): continue
                if isinstance(event, dict):
                    terminal |= event.get("type") in ("result", "error")
                    self.events.put(event)
        finally:
            self.proc.wait()
            if not terminal:
                self.events.put({"type": "error", "attempted": True, "message": "The browser worker stopped unexpectedly."})
            if self.proc.stdout: self.proc.stdout.close()

    def stop(self):
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                try: os.killpg(self.proc.pid, signal.SIGKILL)
                except (AttributeError, ProcessLookupError): self.proc.kill()
                self.proc.wait()
            except ProcessLookupError: pass


def _safe(text):
    return "".join(c if c == "\n" or (ord(c) >= 32 and not 127 <= ord(c) <= 159 and unicodedata.category(c) not in ("Cf", "Cs")) else " " for c in str(text))


def _cell_width(char):
    return 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1


def _lines(text, width):
    width = max(2, width)
    lines, current, cells = [], "", 0
    for char in _safe(text):
        size = _cell_width(char)
        if char == "\n":
            lines.append(current); current, cells = "", 0
        else:
            if cells + size > width: lines.append(current); current, cells = "", 0
            current += char; cells += size
    lines.append(current)
    return lines


def _draw(screen, curses, state):
    screen.erase()
    height, width = screen.getmaxyx()
    if height < 10 or width < 40:
        try: screen.addnstr(0, 0, "Resize terminal to at least 40 × 10.", max(0, width - 1)); screen.refresh()
        except curses.error: pass
        return
    def put(y, x, text, attr=0):
        if 0 <= y < height and 0 <= x < width - 1:
            try: screen.addnstr(y, x, _safe(text).replace("\n", " "), width - x - 1, attr)
            except curses.error: pass
    put(0, 0, " Arena CLI · Interactive chats ", curses.A_BOLD)
    put(1, 0, "F2 New  Tab Next  F4 Previous  F6 Close  Ctrl-Q Quit", curses.A_DIM)
    labels = [f" {i + 1}: {t.title}{' *' if t is state.busy else ''} " for i, t in enumerate(state.tabs)]
    first = state.active; used = sum(map(_cell_width, labels[first]))
    while first > 0 and used + sum(map(_cell_width, labels[first - 1])) < width - 1:
        first -= 1; used += sum(map(_cell_width, labels[first]))
    x = 0
    for i in range(first, len(labels)):
        if x >= width - 1: break
        put(2, x, labels[i], curses.A_REVERSE if i == state.active else curses.A_BOLD)
        x += sum(map(_cell_width, labels[i]))
    put(3, 0, "─" * (width - 1), curses.A_DIM)
    body_height, tab, lines = height - 9, state.tab, []
    if tab.chat and not tab.messages: lines += _lines("Existing chat selected. Earlier messages are not loaded in this view.", width - 2) + [""]
    if not tab.messages and not tab.chat: lines += _lines("Type a message below to start a conversation. /help shows tab commands.", width - 2)
    for role, text in tab.messages: lines += [role + ":"] + _lines(text, width - 2) + [""]
    tab.scroll = min(tab.scroll, max(0, len(lines) - body_height))
    end = max(0, len(lines) - tab.scroll)
    for y, line in enumerate(lines[max(0, end - body_height):end], start=4): put(y, 0, line)
    put(height - 5, 0, state.status, curses.A_DIM)
    put(height - 4, 0, "─" * (width - 1), curses.A_DIM)
    cols, rows, row_cells, cursor_row, cursor_col = width - 3, [""], [0], 0, 0
    for i, c in enumerate(tab.draft):
        c = _safe(c)
        if c != "\n" and row_cells[-1] + _cell_width(c) > cols: rows.append(""); row_cells.append(0)
        if i == tab.cursor: cursor_row, cursor_col = len(rows) - 1, row_cells[-1]
        if c == "\n": rows.append(""); row_cells.append(0)
        else: rows[-1] += c; row_cells[-1] += _cell_width(c)
    if tab.cursor == len(tab.draft): cursor_row, cursor_col = len(rows) - 1, row_cells[-1]
    first = max(0, cursor_row - 1)
    for i, line in enumerate(rows[first:first + 2]): put(height - 3 + i, 0, ("> " if first + i == 0 else "  ") + line)
    put(height - 1, 0, "Ctrl-S Send · Enter New line · PgUp/PgDn Scroll · /help", curses.A_DIM)
    try: curses.curs_set(1); screen.move(height - 3 + cursor_row - first, min(width - 2, 2 + cursor_col)); screen.refresh()
    except curses.error: pass


def validate(ctx, args):
    unsupported = [flag for flag, enabled in (
        ("--json/--format json", ctx.ui.json_mode), ("--jq", getattr(args, "jq", None)),
        ("--dry-run", getattr(args, "dry_run", False)), ("--no-wait", getattr(args, "no_wait", False)),
        ("--quiet", getattr(args, "quiet", False)), ("--verbose", getattr(args, "verbose", False)),
        ("--max-chars", getattr(args, "max_chars", None) is not None)) if enabled]
    if unsupported:
        raise ArenaError("usage", "invalid_argument", "Interactive mode does not support " + ", ".join(unsupported),
                         "Use arena ask for scripting, dry runs, or background jobs")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ArenaError("usage", "terminal_required", "Interactive mode requires a terminal for stdin and stdout",
                         "Run arena tui in a terminal, or use arena ask for scripts")
    try: import curses  # noqa: F401
    except ImportError:
        raise ArenaError("config", "terminal_unsupported", "This Python installation does not support curses",
                         "Use arena ask, or run arena tui on a Unix-like system with curses") from None


def _request(ctx, tab, prompt):
    return {"config": ctx.engine_cfg, "state_file": str(ctx.paths.state_file),
            "profile_dir": str(ctx.paths.profile_dir), "chat": tab.chat, "prompt": prompt,
            "headed": bool(getattr(ctx.args, "headed", False)), "timeout": ctx.get("timeout"), "stable": ctx.get("stable")}


def _run_screen(screen, ctx, curses, worker_factory=Worker):
    state, worker = ChatState(getattr(ctx.args, "chat", None)), None
    screen.keypad(True); curses.raw(); screen.timeout(100)
    try:
        while True:
            if worker is not None:
                while True:
                    try: event = worker.events.get_nowait()
                    except queue.Empty: break
                    state.event(event)
                if state.busy is None: worker = None
            _draw(screen, curses, state)
            try: key = screen.get_wch()
            except curses.error: continue
            tab = state.tab
            if key in ("\x11", "\x03", curses.KEY_F10):
                if state.busy and not state.confirm_quit:
                    state.confirm_quit = True
                    state.status = "A request is running. Press Ctrl-Q again to stop waiting; it may already have been sent."
                    continue
                break
            height, width = screen.getmaxyx()
            if height < 10 or width < 40: continue
            if key == curses.KEY_F2: state.confirm_quit = False; state.new_tab()
            elif key in ("\t", curses.KEY_F3): state.confirm_quit = False; state.switch(1)
            elif key in (curses.KEY_BTAB, curses.KEY_F4): state.confirm_quit = False; state.switch(-1)
            elif key == curses.KEY_F6: state.confirm_quit = False; state.close_tab()
            elif key == curses.KEY_PPAGE: tab.scroll += max(1, height - 10)
            elif key == curses.KEY_NPAGE: tab.scroll = max(0, tab.scroll - max(1, height - 10))
            elif key == curses.KEY_LEFT: tab.cursor = max(0, tab.cursor - 1)
            elif key == curses.KEY_RIGHT: tab.cursor = min(len(tab.draft), tab.cursor + 1)
            elif key == curses.KEY_UP:
                start = tab.draft.rfind("\n", 0, tab.cursor) + 1
                if start:
                    prev = tab.draft.rfind("\n", 0, start - 1) + 1
                    tab.cursor = prev + min(tab.cursor - start, start - prev - 1)
            elif key == curses.KEY_DOWN:
                start = tab.draft.rfind("\n", 0, tab.cursor) + 1
                end = tab.draft.find("\n", tab.cursor)
                if end >= 0:
                    next_end = tab.draft.find("\n", end + 1)
                    if next_end < 0: next_end = len(tab.draft)
                    tab.cursor = end + 1 + min(tab.cursor - start, next_end - end - 1)
            elif key == curses.KEY_HOME: tab.cursor = tab.draft.rfind("\n", 0, tab.cursor) + 1
            elif key == curses.KEY_END:
                end = tab.draft.find("\n", tab.cursor)
                tab.cursor = len(tab.draft) if end < 0 else end
            elif key in ("\b", "\x7f", curses.KEY_BACKSPACE):
                if tab.cursor:
                    tab.draft = tab.draft[:tab.cursor - 1] + tab.draft[tab.cursor:]; tab.cursor -= 1
            elif key == curses.KEY_DC: tab.draft = tab.draft[:tab.cursor] + tab.draft[tab.cursor + 1:]
            elif key == "\x13":
                text = tab.draft.strip()
                if not text: continue
                if text.startswith("/") and not text.startswith("//"):
                    tab.draft, tab.cursor = "", 0
                    if state.command(text) == "quit":
                        if state.busy and not state.confirm_quit:
                            state.confirm_quit = True
                            state.status = "A request is running. Use /quit again to stop waiting; it may already have been sent."
                        else: break
                    continue
                prompt = text[1:] if text.startswith("//") else text
                if state.begin(prompt):
                    tab.draft, tab.cursor = "", 0
                    try: worker = worker_factory(_request(ctx, tab, prompt))
                    except (OSError, ValueError) as exc:
                        state.event({"type": "error", "attempted": False, "message": f"Could not start browser worker: {type(exc).__name__}"})
            elif key in ("\n", "\r", curses.KEY_ENTER, "\x0f"):
                tab.draft = tab.draft[:tab.cursor] + "\n" + tab.draft[tab.cursor:]; tab.cursor += 1
            elif isinstance(key, str) and key.isprintable():
                tab.draft = tab.draft[:tab.cursor] + key + tab.draft[tab.cursor:]; tab.cursor += 1
    finally:
        if worker is not None: worker.stop()


def run(ctx, args):
    validate(ctx, args)
    import curses
    try: curses.wrapper(lambda screen: _run_screen(screen, ctx, curses))
    except curses.error:
        raise ArenaError("config", "terminal_unsupported", "Could not initialize the terminal interface",
                         "Check TERM and run in a terminal with curses support") from None
    return 0
