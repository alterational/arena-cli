#!/usr/bin/env python3
"""Offline browser tests for follow-ups, origins, and scoped optimizations."""
from pathlib import Path
import sys, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright
from arena.engine import DEFAULT_CONFIG, Session, SkillError

HTML='''<!doctype html><body>
<div id="msgs"><div data-agent-transcript-message><div data-user-message-body-row>old question</div></div>
<div data-agent-transcript-message><div class="prose">previous answer</div></div></div>
<div class="tiptap ProseMirror" contenteditable="true"></div><button aria-label="Send message">Send</button>
<script>document.querySelector('button').onclick = () => {
 const ed=document.querySelector('.tiptap'),text=ed.innerText;ed.innerText='';
 const user=document.createElement('div');user.setAttribute('data-agent-transcript-message','');
 const body=document.createElement('div');body.setAttribute('data-user-message-body-row','');body.textContent=text;
 user.appendChild(body);document.querySelector('#msgs').appendChild(user);
 setTimeout(()=>{const t=document.createElement('div');t.setAttribute('data-agent-transcript-message','');
 const a=document.createElement('div');a.className='prose';a.textContent=window.nextAnswer;t.appendChild(a);
 document.querySelector('#msgs').appendChild(t)},window.answerDelay);
};</script></body>'''

class BrowserTests(unittest.TestCase):
    def test_followup_waits_for_new_and_repeated_answers(self):
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            try:
                for answer,delay,timeout,expected in [('new answer',1800,6,'new answer'),('previous answer',1800,6,'previous answer'),('too late',6000,1,None)]:
                    page=browser.new_page();page.set_content(HTML);page.evaluate('([a,d])=>{window.nextAnswer=a;window.answerDelay=d}',[answer,delay])
                    session=Session(dict(DEFAULT_CONFIG),Path('unused'),Path('unused'));session.page=page
                    with patch.object(session,'open_chat',return_value=page),patch.object(session,'handle_terms'),patch.object(session,'_check_page_origin'):
                        if expected is None:
                            with self.assertRaises(SkillError) as exc: session.ask('new question',chat='existing',timeout=timeout,stable=.5,poll=.2)
                            self.assertEqual(exc.exception.code,'TIMEOUT')
                        else: self.assertEqual(session.ask('new question',chat='existing',timeout=timeout,stable=.5,poll=.2)['answer'],expected)
                    page.close()
            finally: browser.close()

    def test_optimization_is_scoped_and_script_only(self):
        seen=[]
        html='''<html><head><script src="https://www.google-analytics.com/a.js"></script><script src="/rpc/test.js"></script></head><body><div class="effect" style="transition:opacity 3s">ok</div></body></html>'''
        with sync_playwright() as pw:
            browser=pw.chromium.launch();ctx=browser.new_context()
            def fulfill(route):
                seen.append((route.request.url,route.request.resource_type))
                if route.request.resource_type=='document': route.fulfill(status=200,body=html,content_type='text/html')
                else: route.fulfill(status=200,body='ok',content_type='text/plain')
            ctx.route('**/*',fulfill)
            s=Session(dict(DEFAULT_CONFIG),Path('unused'),Path('unused'));s.ctx=ctx;s._configure_performance()
            page=ctx.new_page();page.goto('https://arena.ai/agent',wait_until='load')
            self.assertFalse(any('google-analytics' in u or '/rpc/test.js' in u for u,_ in seen),seen)
            self.assertEqual(page.locator('.effect').evaluate('(e)=>getComputedStyle(e).transitionDuration'),'0s')
            page.evaluate("fetch('/rpc/data')");page.wait_for_timeout(300)
            self.assertTrue(any('/rpc/data' in u for u,_ in seen),seen)
            seen.clear();page.goto('https://outside.example/',wait_until='load')
            self.assertTrue(any('google-analytics' in u for u,_ in seen),seen)
            ctx.close();browser.close()

    def test_cross_origin_redirect_never_reaches_destination(self):
        class Target(BaseHTTPRequestHandler):
            hits=0
            def log_message(self,*_): pass
            def do_GET(self): type(self).hits+=1;self.send_response(200);self.end_headers()
        target=ThreadingHTTPServer(('127.0.0.1',0),Target)
        class Source(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{target.server_port}/out');self.end_headers()
        source=ThreadingHTTPServer(('127.0.0.1',0),Source)
        threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (source,target)]
        for t in threads:t.start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch();ctx=browser.new_context();page=ctx.new_page()
                base=f'http://127.0.0.1:{source.server_port}';s=Session({**DEFAULT_CONFIG,'base_url':base},Path('unused'),Path('unused'));s.ctx=ctx;s.page=page
                with self.assertRaises(SkillError):s.open_agent()
                with self.assertRaises(SkillError):s.workspace_bytes('/file')
                self.assertEqual(Target.hits,0);ctx.close();browser.close()
        finally:
            for server in (source,target):server.shutdown();server.server_close()
            for t in threads:t.join()

if __name__=='__main__':unittest.main()
