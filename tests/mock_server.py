#!/usr/bin/env python3
"""
mock_server.py — 本地假站点，用来端到端验证 arena_agent.py 的自动化逻辑。

它尽量复刻 arena.ai/agent 的 DOM 结构：
  * 输入框：<div class="tiptap ProseMirror" contenteditable="true">
  * 发送按钮：<button aria-label="Send message">
  * 首次进入的 Terms 弹窗（role=dialog + "Terms of Use" + Agree 按钮）
  * 未登录时点发送 -> 弹出 "Log In or Create Account"（Google / email 两种入口）
  * 登录后发送 -> 用户气泡 + 助手消息流式输出（带 Stop 按钮）

用法：python tests/mock_server.py [--port 8765]
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
  <a href="/agent/chat-aaaaaaaa-0001">用上传的 cookies 提问</a>
  <a href="/agent/chat-bbbbbbbb-0002">多行中文 prompt 测试</a>
  <a href="/agent/chat-cccccccc-0003">长文本与特殊字符</a>
  <a href="/history/search">Search</a>
</nav>
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
  '<button aria-label="Account menu">Ava</button>';   // 登录后才有的账号入口
document.getElementById('terms').classList.remove('hidden');
document.getElementById('agree').onclick = () => {{
  document.getElementById('terms').remove();          // 与线上一致：关闭即卸载
  fetch('/agree', {{method:'POST'}});
}};
document.getElementById('emailBtn').onclick = () => {{
  const v = document.querySelector('input[type=email]').value;
  if (!v || !v.includes('@')) {{ alert('Email is required'); return; }}
  fetch('/login', {{method:'POST', headers:{{'content-type':'application/json'}},
    body: JSON.stringify({{email:v}})}}).then(() => location.reload());
}};

// DOM 结构对齐真实的 arena.ai：
//   每轮 = [data-agent-transcript-message]，用户轮内含 [data-user-message-body-row]，
//   正文在 .prose，时间戳/反馈等 UI 噪音在 .prose 之外。
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
  if (!loggedIn) {{ login.classList.remove('hidden'); return; }}   // 登录墙：消息不发出
  // 真实站点发送前会 POST /nextjs-api/stream/create-chat；限流时这里返回 429
  fetch('/nextjs-api/stream/create-chat', {{method: 'POST'}}).then(r => {{
    if (!r.ok) {{                       // 服务端拒绝：把乐观渲染的消息整条撤回（复刻线上行为）
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
  const answer = 'ECHO[' + text.replace(/\\n/g, ' / ') + '] 这是模拟的 Agent 回答。';
  let i = 0;
  const timer = setInterval(() => {{
    out.textContent = answer.slice(0, ++i);
    if (i >= answer.length) {{ clearInterval(timer); stop.remove(); }}
  }}, 12);
}}
// 复刻线上：会话页（/agent/<id>）底部是「Was this task successful?」反馈卡，输入框被它顶掉
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


class Handler(BaseHTTPRequestHandler):
    state = {"terms_ok": False, "logged_in": False, "emails": [], "rate_limit": False}

    def log_message(self, *_a):  # 安静点
        pass

    def _html(self, body: str, code: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        cls = type(self)
        cookie = self.headers.get("cookie") or ""
        cls.state["logged_in"] = "mock_session=1" in cookie
        # /agent?ratelimit=1 -> 之后每次发送都返回 429（复刻真实站点的限流）
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
            # 关键：真实站点登录成功后写的是 httpOnly cookie，这里用普通 cookie 模拟
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
