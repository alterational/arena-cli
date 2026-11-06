#!/usr/bin/env python3
"""Offline pseudo-terminal test of the full UI and mock browser."""
import json, os, select, struct, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
try:
    import fcntl, pty, termios
except ImportError:
    fcntl = pty = termios = None
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mock_server


@unittest.skipUnless(fcntl and pty and termios, 'pseudo-terminal unavailable')
class TerminalTests(unittest.TestCase):
    def test_tabs_followup_and_no_retry(self):
        original=mock_server.HTML
        mock_server.HTML=original.replace('function doSend(text) {', """function doSend(text) {
if (location.pathname === '/agent') history.pushState({}, '', '/agent/chat-' + Math.random().toString(16).slice(2) + '-0001');
""")
        class Handler(mock_server.Handler):
            paths=[]; sends=0; state=dict(mock_server.Handler.state,rate_limit=False)
            def do_GET(self): type(self).paths.append(self.path); return super().do_GET()
            def do_POST(self):
                if self.path.startswith('/nextjs-api/stream/create-chat'): type(self).sends += 1
                return super().do_POST()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        master=slave=proc=None
        try:
            with tempfile.TemporaryDirectory(prefix='arena-tui-test-') as d:
                Path(d,'state.json').write_text(json.dumps({'cookies':[{'name':'mock_session','value':'1','domain':'127.0.0.1','path':'/','expires':-1,'httpOnly':False,'secure':False,'sameSite':'Lax'}],'origins':[]}))
                master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',30,100,0,0))
                env={**os.environ,'ARENA_HOME':d,'TERM':'xterm-256color','HTTPS_PROXY':'','HTTP_PROXY':'','ALL_PROXY':'','NO_COLOR':'1'}
                proc=subprocess.Popen([sys.executable,'-m','arena','--base-url',f'http://127.0.0.1:{server.server_port}','--timeout','10','--stable','0.5','tui'],stdin=slave,stdout=slave,stderr=slave,env=env,close_fds=True)
                os.close(slave);slave=None
                def wait_for(text,seconds=20):
                    data=bytearray();deadline=time.monotonic()+seconds
                    while time.monotonic()<deadline:
                        ready,_,_=select.select([master],[],[],0.2)
                        if ready:
                            try: data.extend(os.read(master,65536))
                            except OSError: break
                            if text.encode() in data: return
                        if proc.poll() is not None: break
                    raise AssertionError(f'Missing {text!r}; terminal tail: {bytes(data[-1000:])!r}')
                wait_for('Interactive chats');os.write(master,b'first prompt\x13');wait_for('Response complete.')
                os.write(master,b'followup\x13');wait_for('Response complete.')
                os.write(master,b'/new\x13second tab\x13');wait_for('Response complete.')
                Handler.state['rate_limit']=True
                os.write(master,b'/new\x13rejected request\x13');wait_for('Request failed; no automatic retry.')
                self.assertEqual(Handler.sends,4)
                os.write(master,b'another message\x13');wait_for('conversation could not be identified');self.assertEqual(Handler.sends,4)
                os.write(master,b'\x11');self.assertEqual(proc.wait(timeout=10),0)
                self.assertEqual(len([p for p in Handler.paths if p.startswith('/agent/chat-')]),1,Handler.paths)
        finally:
            if proc and proc.poll() is None: proc.terminate();proc.wait(timeout=10)
            if slave is not None: os.close(slave)
            if master is not None: os.close(master)
            server.shutdown();server.server_close();thread.join();mock_server.HTML=original


if __name__ == '__main__': unittest.main()
