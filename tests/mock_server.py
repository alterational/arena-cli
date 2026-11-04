#!/usr/bin/env python3
"""
mock_server.py — local fake site, used to verify the automation logic of arena_agent.py end to end.

It closely replicates the DOM structure of arena.ai/agent:
  * Input box: <div class="tiptap ProseMirror" contenteditable="true">
  * Send button: <button aria-label="Send message">
  * Terms dialog shown on first visit (role=dialog + "Terms of Use" + Agree button)
  * When not logged in, clicking send -> "Log In or Create Account" pops up (Google / email entry points)
  * After logging in, sending -> user bubble + streamed assistant output (with a Stop button)

Usage: python tests/mock_server.py [--port 8765]
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Mock Arena Agent</title>
<style>
 body{{font-family:system-ui;margin:0;background:#1b1b1b;color:#eee}}
 #wrap{{max-width:760px;margin:60px auto}}
 .row{{display:flex;gap:8px;align-items:flex-end;margin-top:24px}}
 .tiptap{{flex:1;min-height:80px;border:1px solid #444;border-radius:8px;padding:12px;background:#262626}}
 button{{background:#e8e5d8;color:#111;border:0;border-radius:6px;padding:10px 14px;cursor:pointer}}
 #sidebar a{{display:block;color:#bbb;font-size:13px;padding:4px 0;text-decoration:none}}
 #msgs>div{{margin:14px 0;padding:12px;border-radius:8px;background:#242424;white-space:pre-wrap}}
 #msgs .prose{{white-space:pre-wrap}}
 #feedback{{border:1px solid #444;border-radius:10px;padding:10px 14px;background:#242424;margin-top:16px}}
 #feedback .fb-head{{display:flex;justify-content:space-between;align-items:center;gap:16px}}
 #feedback .esc{{color:#888;font-size:12px;margin-left:6px}}
 #feedback .fb{{display:block;width:100%;text-align:left;margin-top:8px;background:#2e2e2e;color:#eee}}
 #feedback #fbclose{{background:transparent;color:#888;font-size:14px;padding:2px 6px}}
 [role=dialog]{{position:fixed;inset:0;margin:auto;width:420px;height:fit-content;background:#2a2a2a;
   border:1px solid #555;border-radius:10px;padding:20px;display:flex;flex-direction:column;gap:10px}}
 input{{padding:10px;border-radius:6px;border:1px solid #555;background:#1b1b1b;color:#eee}}
 .hidden{{display:none!important}}
</style></head><body>
<nav id="sidebar">
  <a href="/agent/chat-aaaaaaaa-0001">Ask with the uploaded cookies</a>
  <a href="/agent/chat-bbbbbbbb-0002">Multi-line prompt test (non-ASCII)</a>
  <a href="/agent/chat-cccccccc-0003">Long text and special characters</a>
  <a href="/history/search">Search</a>
</nav>
<div id="ws" class="max-h-[400px] border-border-medium bg-surface-primary flex flex-col overflow-hidden rounded-md border">
  <div class="flex items-center justify-between">
    <span>Workspace</span>
    <a href="/api/chat/chat-aaaaaaaa-0001/workspace/wsid/download" download="" aria-label="Download workspace">&#11015;</a>
  </div>
  <div><span>3/128.0MB</span> <span>3/10K files</span></div>
  <div id="wstree">
    <button type="button" aria-label="Collapse uploads folder" style="padding-left: 8px;">uploads</button>
    <div role="button" tabindex="0" aria-label="note.txt file" style="padding-left: 24px;">
      <span>note.txt</span>
      <a href="/api/chat/workspace/cas/user/note" download="note.txt" aria-label="Download note.txt">&#11015;</a>
    </div>
    <div role="button" tabindex="0" aria-label="hello.md file" style="padding-left: 8px;">
      <span>hello.md</span>
      <a href="/api/chat/workspace/cas/user/readme" download="hello.md" aria-label="Download hello.md">&#11015;</a>
    </div>
    <div role="button" tabindex="0" aria-label="image.png file" style="padding-left: 8px;">
      <span>image.png</span>
      <a href="/api/chat/workspace/cas/user/img" download="image.png" aria-label="Download image.png">&#11015;</a>
    </div>
  </div>
</div>

<div id="wrap">
  <h3>Mock Arena — Agent Mode <span id="acct"></span></h3>
  <div id="msgs"></div>
  <div class="row">
    <div class="tiptap ProseMirror" contenteditable="true"></div>
    <button aria-label="Send message" id="send">&#8594;</button>
  </div>
</div>

<div id="feedback" class="hidden">
  <div class="fb-head"><b>Was this task successful?</b><span><button id="fbclose" aria-label="Close">&#10005;</button><span class="esc">Esc</span></span></div>
  <button class="fb">Yes</button>
  <button class="fb">No</button>
  <button class="fb">Keep working</button>
</div>

<div role="dialog" id="terms" class="hidden">
  <b>Terms of Use &amp; Privacy Policy</b>
  <div>Please accept the terms to continue.</div>
  <button id="agree">Agree</button>
</div>

<div role="dialog" id="login" class="hidden">
  <b>Log In or Create Account</b>
  <div>Your current chat history will be saved to your new account.</div>
  <button id="google">Continue with Google</button>
  <div style="text-align:center;opacity:.6">OR</div>
  <input type="email" name="email" placeholder="Your email" autocomplete="email">
  <button id="emailBtn">Continue with email</button>
</div>

<script>
const loggedIn = document.cookie.includes('mock_session=1');
const msgs = document.getElementById('msgs');
const ed = document.querySelector('.tiptap');
const login = document.getElementById('login');
if (loggedIn) document.getElementById('acct').innerHTML =
  '<button aria-label="Account menu">Ava</button>';   // Account entry that only appears after login
document.getElementById('terms').classList.remove('hidden');
document.getElementById('agree').onclick = () => {{
  document.getElementById('terms').remove();          // Matches production: closing it unmounts it
  fetch('/agree', {{method:'POST'}});
}};
document.getElementById('emailBtn').onclick = () => {{
  const v = document.querySelector('input[type=email]').value;
  if (!v || !v.includes('@')) {{ alert('Email is required'); return; }}
  fetch('/login', {{method:'POST', headers:{{'content-type':'application/json'}},
    body: JSON.stringify({{email:v}})}}).then(() => location.reload());
}};

// The DOM structure matches the real arena.ai:
//   each turn = [data-agent-transcript-message]; a user turn contains [data-user-message-body-row],
//   the body is in .prose, and UI noise such as timestamps and feedback sits outside .prose.
function addMsg(role, text) {{
  const d = document.createElement('div');
  d.setAttribute('data-agent-transcript-message', 'true');
  d.setAttribute('data-role', role);
  if (role === 'user') {{
    const row = document.createElement('div');
    row.setAttribute('data-user-message-body-row', '');
    row.innerHTML = '<div class="prose"></div>';
    row.querySelector('.prose').textContent = text;
    d.appendChild(row);
    msgs.appendChild(d);
    return row.querySelector('.prose');
  }}
  d.innerHTML = '<div class="prose"></div><div class="meta">1s | just now — Was this task successful?</div>';
  d.querySelector('.prose').textContent = text;
  msgs.appendChild(d);
  return d.querySelector('.prose');
}}

function send() {{
  const text = ed.innerText.trim();
  if (!text) return;
  if (!loggedIn) {{ login.classList.remove('hidden'); return; }}   // Login wall: the message is not sent
  // The real site POSTs to /nextjs-api/stream/create-chat before sending; under rate limiting this returns 429
  fetch('/nextjs-api/stream/create-chat', {{method: 'POST'}}).then(r => {{
    if (!r.ok) {{                       // Server rejected it: retract the optimistically rendered message entirely (replicates production)
      msgs.innerHTML = '';
      ed.innerText = text;
      return;
    }}
    doSend(text);
  }});
}}

function doSend(text) {{
  ed.innerHTML = '';
  addMsg('user', text);
  const out = addMsg('assistant', '');
  const stop = document.createElement('button');
  stop.setAttribute('aria-label', 'Stop response');
  stop.textContent = 'Stop';
  document.body.appendChild(stop);
  const answer = 'ECHO[' + text.replace(/\\n/g, ' / ') + '] This is a simulated Agent answer.';
  let i = 0;
  const timer = setInterval(() => {{
    out.textContent = answer.slice(0, ++i);
    if (i >= answer.length) {{ clearInterval(timer); stop.remove(); }}
  }}, 12);
}}
// Replicates production: the bottom of the conversation page (/agent/<id>) shows a "Was this task successful?" feedback card that pushes the input box out of view
const feedback = document.getElementById('feedback');
const composerRow = document.querySelector('.row');
const isChatPage = /^\\/agent\\/[^/]+/.test(location.pathname);
function showFeedback() {{ feedback.classList.remove('hidden'); composerRow.classList.add('hidden'); }}
function hideFeedback() {{ feedback.classList.add('hidden'); composerRow.classList.remove('hidden'); }}
if (isChatPage) {{ showFeedback(); }}
document.getElementById('fbclose').onclick = hideFeedback;
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') hideFeedback(); }});

document.getElementById('send').onclick = send;
ed.addEventListener('keydown', e => {{
  if (e.key === 'Enter' && !e.shiftKey) {{ e.preventDefault(); send(); }}
}});
</script></body></html>
"""


HTML = PAGE.replace("{{", "{").replace("}}", "}")

# Fake workspace files: cas key -> (path inside the zip, content, content-type)
WORKSPACE_FILES: dict[str, tuple[str, bytes, str]] = {
    "note": ("uploads/note.txt", "hello from mock\nsecond line\n".encode(), "text/plain; charset=utf-8"),
    "readme": ("hello.md", "# Mock workspace\n\n- item\n".encode(), "text/markdown; charset=utf-8"),
    "img": ("image.png", bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 12, "image/png"),
}


def _workspace_zip() -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data, _ctype in WORKSPACE_FILES.values():
            zf.writestr(path, data)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    state = {"terms_ok": False, "logged_in": False, "emails": [], "rate_limit": False}

    def log_message(self, *_a):  # quiet
        pass

    def _html(self, body: str, code: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _raw(self, data: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        cls = type(self)
        cookie = self.headers.get("cookie") or ""
        cls.state["logged_in"] = "mock_session=1" in cookie
        # Workspace file: /api/chat/workspace/cas/user/<key>
        if self.path.startswith("/api/chat/workspace/cas/user/"):
            key = self.path.rsplit("/", 1)[-1].split("?")[0]
            item = WORKSPACE_FILES.get(key)
            if not item:
                self.send_response(404)
                self.end_headers()
                return
            self._raw(item[1], item[2])
            return
        # Whole workspace packaged: /api/chat/<chat>/workspace/<id>/download
        if "/workspace/" in self.path and self.path.endswith("/download"):
            self._raw(_workspace_zip(), "application/zip",
                      {"content-disposition": 'attachment; filename="workspace.zip"'})
            return
        # /agent?ratelimit=1 -> every later send returns 429 (replicates the real site's rate limiting)
        if "ratelimit=1" in self.path:
            cls.state["rate_limit"] = True
        elif "ratelimit=0" in self.path:
            cls.state["rate_limit"] = False
        self._html(HTML)

    def do_POST(self):
        cls = type(self)
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        extra = {}
        if self.path.startswith("/nextjs-api/stream/create-chat"):
            if cls.state.get("rate_limit"):
                self.send_response(429)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"rate limited"}')
                return
            self.send_response(204)
            self.end_headers()
            return
        if self.path.startswith("/agree"):
            cls.state["terms_ok"] = True
        elif self.path.startswith("/login"):
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            cls.state["emails"].append(body.get("email"))
            cls.state["logged_in"] = True
            # Key point: the real site writes an httpOnly cookie after login; a plain cookie is used here to simulate it
            extra["Set-Cookie"] = "mock_session=1; Path=/; Max-Age=2592000; SameSite=Lax"
        self.send_response(204)
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()


def serve(port: int = 0) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    s = serve(a.port)
    print(f"mock arena listening on http://127.0.0.1:{s.server_address[1]}/agent")
    s.serve_forever()
