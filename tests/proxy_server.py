"""tests/proxy_server.py — minimal forward proxy, used specifically to verify that `arena --proxy` really goes through the proxy.

Only handles plain HTTP (absolute-URI form such as `GET http://host:port/path`); enough to test the mock site;
a browser accessing an http:// site uses exactly this form. CONNECT (https) is not implemented here.

Usage:

    from proxy_server import serve
    srv = serve(0); threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]           # then pass --proxy http://127.0.0.1:<port>
    srv.state["requests"]                  # list of requests the proxy received
"""

from __future__ import annotations

import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class Handler(BaseHTTPRequestHandler):
    state: dict[str, list] = {"requests": [], "errors": []}

    def log_message(self, *_a):  # quiet
        pass

    def do_GET(self):  # noqa: N802
        cls = type(self)
        url = self.path
        if not url.lower().startswith("http://"):
            # Not an absolute URI: it was not being used as a proxy
            cls.state["errors"].append(f"not-proxy-request: {url[:80]}")
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"this is a proxy; use absolute-URI requests")
            return
        cls.state["requests"].append(url)
        try:
            req = urllib.request.Request(url, headers={
                k: v for k, v in self.headers.items()
                if k.lower() not in ("proxy-connection", "proxy-authorization", "host")
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                self.send_response(resp.status)
                self.send_header("content-type", resp.headers.get("content-type", "application/octet-stream"))
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as exc:  # pass the status code through
            self.send_response(exc.code)
            self.send_header("content-length", "0")
            self.end_headers()
        except Exception as exc:  # noqa: BLE001
            cls.state["errors"].append(f"{urlsplit(url).netloc}: {type(exc).__name__}: {exc}")
            self.send_response(502)
            self.send_header("content-length", "0")
            self.end_headers()


def serve(port: int = 0) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    type(srv).state = Handler.state  # so tests can use srv.state[...]
    Handler.state["requests"].clear()
    Handler.state["errors"].clear()
    return srv
