"""Offline regression tests for safe configuration, follow-ups, and tabs."""
import json
import os
import queue
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from arena.commands.tui import ChatState, _run_screen, _safe
from arena.config import profile_paths
from arena.engine import DEFAULT_CONFIG, Session, SkillError
from arena.errors import ArenaError
from arena.url_safety import same_origin_url

ROOT = Path(__file__).resolve().parent.parent


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = {**os.environ, 'ARENA_HOME': self.tmp.name, 'NO_COLOR': '1', 'HTTPS_PROXY':'', 'ALL_PROXY':''}
    def tearDown(self): self.tmp.cleanup()
    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'arena', *args], cwd=ROOT, env=self.env, text=True, capture_output=True, timeout=10)

    def test_cookie_dry_run_does_not_write(self):
        target = self.home / 'state.json'; target.write_text('original')
        cookies = self.home / 'input.txt'; cookies.write_text('# Netscape HTTP Cookie File\n.arena.ai\tTRUE\t/\tTRUE\t4102444800\t__session\tsynthetic\n')
        result = self.cli('--json','--dry-run','--cookies',str(cookies),'ask','test')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_text(), 'original')
        self.assertTrue(json.loads(result.stdout)['dry_run'])

    def test_profiles_confined_and_config_private(self):
        for name in ('../else', '.', '..', 'a/b', 'x\\y', 'bad\0name'):
            with self.subTest(name=name), self.assertRaises(ArenaError): profile_paths(name, self.home)
        root=self.home/'profiles'; root.mkdir(); (root/'real').mkdir(); (root/'alias').symlink_to(root/'real')
        with self.assertRaises(ArenaError): profile_paths('alias',self.home)
        result=self.cli('--json','config','set','proxy','http://user:secret@127.0.0.1:9999?token=secret')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertNotIn('secret',result.stdout+result.stderr)
        self.assertEqual((self.home/'config.json').stat().st_mode & 0o777,0o600)
        self.assertNotIn('secret',self.cli('--json','config','list').stdout)
        from arena.engine import parse_proxy
        with self.assertRaises(SkillError) as exc:
            parse_proxy('http://username:secret@localhost:badport')
        self.assertNotIn('secret', str(exc.exception))

    def test_invalid_base_and_redirect_origins(self):
        for url in ('https://evil.example/', 'http://user:secret@localhost/', 'file:///tmp/x', 'http://localhost:0/', 'http://localhost/\nabc'):
            with self.subTest(url=url), self.assertRaises(ValueError): same_origin_url('http://localhost',url)
        self.assertEqual(same_origin_url('http://localhost','/file name'),'http://localhost/file name')
        result=self.cli('--json','--base-url','https://user:pass@arena.ai','status')
        self.assertEqual(result.returncode,1); self.assertFalse(json.loads(result.stdout)['ok'])
        session=Session({**DEFAULT_CONFIG,'base_url':'http://localhost'},self.home/'p',self.home/'s')
        response=Mock(status=302, headers={'location':'https://evil.example/file'})
        session.ctx=Mock(); session.ctx.request.get.return_value=response
        with self.assertRaises(SkillError): session.workspace_bytes('/file')
        session.ctx.request.get.assert_called_once()

    def test_state_filter_and_private_jobs(self):
        from arena.jobs import _write_json, cancel
        path=self.home/'state.json';path.write_text(json.dumps({'cookies':[{'name':'ok','domain':'.example.com'}, {'name':'other','domain':'evil.example'}], 'origins':[{'origin':'https://example.com','localStorage':[]},{'origin':'https://evil.example','localStorage':[]}]}))
        session=Session({**DEFAULT_CONFIG,'base_url':'https://example.com'},self.home/'p',path);session.ctx=Mock();session._restore_state()
        self.assertEqual([c['name'] for c in session.ctx.add_cookies.call_args.args[0]],['ok'])
        self.assertNotIn('evil.example',session.ctx.add_init_script.call_args.kwargs['script'])
        job=self.home/'jobs'/'sample';job.mkdir(parents=True);_write_json(job/'job.json',{'status':'done','pid':os.getpid()})
        self.assertEqual((job/'job.json').stat().st_mode & 0o777,0o600)
        with patch('arena.jobs.os.kill') as kill: self.assertFalse(cancel(job)); kill.assert_not_called()

    def test_tabs_never_retry_and_keep_identity(self):
        state=ChatState(); first=state.tab; self.assertTrue(state.begin('hello'));state.new_tab();second=state.tab
        self.assertFalse(state.begin('blocked while busy'))
        state.event({'type':'progress','text':'partial'});state.event({'type':'result','answer':'done','chat_id':'chat-aaaaaaaa'})
        self.assertEqual(first.chat,'chat-aaaaaaaa');self.assertEqual(second.messages,[])
        state.begin('second');state.event({'type':'error','attempted':True,'message':'timeout'})
        self.assertTrue(second.uncertain);self.assertFalse(state.begin('do not retry'))
        self.assertEqual(_safe('x\x1b[31m\x9b\u202ey'),'x [31m  y')

    def test_tui_cli_validation(self):
        r=self.cli('--json','tui');self.assertEqual(r.returncode,1);self.assertEqual(json.loads(r.stdout)['error']['subtype'],'invalid_argument')
        r=self.cli('tui');self.assertEqual(r.returncode,1);self.assertIn('requires a terminal',r.stderr)
        r=self.cli('--json','schema','tui');self.assertEqual(json.loads(r.stdout)['data']['risk'],'write')

    def test_screen_input_and_tab_routing(self):
        try: import curses
        except ImportError: self.skipTest('curses unavailable')
        requests=[]
        class FakeWorker:
            def __init__(self, req):
                requests.append(dict(req));self.events=queue.Queue(); self.events.put({'type':'result','answer':'answer','chat_id':req['chat'] or f'chat-{len(requests):08d}'})
            def stop(self): pass
        keys=list('first\nline')+['\x13']+list('followup')+['\x13',curses.KEY_F2]+list('other')+['\x13',curses.KEY_F4]+list('again')+['\x13','\x11']
        class Screen:
            def keypad(self,*a): pass
            def timeout(self,*a): pass
            def erase(self): pass
            def getmaxyx(self): return 24,100
            def addnstr(self,*a): pass
            def move(self,*a): pass
            def refresh(self): pass
            def get_wch(self): return keys.pop(0)
        ctx=SimpleNamespace(args=SimpleNamespace(chat=None,headed=False),engine_cfg={},paths=SimpleNamespace(state_file=Path('s'),profile_dir=Path('p')),get=lambda k:{'timeout':10,'stable':1}[k])
        with patch.object(curses,'raw'),patch.object(curses,'curs_set'): _run_screen(Screen(),ctx,curses,FakeWorker)
        self.assertEqual([r['prompt'] for r in requests],['first\nline','followup','other','again'])
        self.assertEqual([r['chat'] for r in requests],[None,'chat-00000001',None,'chat-00000001'])


if __name__ == '__main__': unittest.main()
