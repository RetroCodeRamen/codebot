"""The GUI's web server: 127.0.0.1 only. GET serves the page (lib/codebot/gui/static) and the read API; POST changes state and must come from
the page itself: header X-Codebot: 1 (a cross-site form cannot set it, and a cross-site fetch that sets it needs a CORS preflight this server never
grants), an Origin equal to this server's, and a Host of 127.0.0.1/localhost at this port (no DNS rebinding). The page loads nothing from
elsewhere and runs no inline code (Content-Security-Policy), so generated files, prompts and logs it shows can never execute in it."""
import http.server, json, mimetypes, os, socketserver, sys, traceback, urllib.parse
from . import api

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
MAX_BODY = 1 << 20

class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    def __init__(self, port, ctx):
        self.ctx = ctx; super().__init__(("127.0.0.1", port), Handler)
        p = self.server_address[1]
        self.hosts = {f"127.0.0.1:{p}", f"localhost:{p}"}; self.origins = {f"http://{h}" for h in self.hosts}

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "codebot-gui"; sys_version = ""
    def log_message(self, fmt, *args): pass                        # request lines can carry file paths; nothing is logged

    def _headers(self, code, ctype, length, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", CSP); self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer"); self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin"); self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()

    def _json(self, code, obj):
        data = json.dumps(obj, default=str).encode()
        self._headers(code, "application/json; charset=utf-8", len(data), {"Cache-Control": "no-store"}); self.wfile.write(data)

    def _host_ok(self): return self.headers.get("Host", "") in self.server.hosts

    def do_GET(self):
        if not self._host_ok(): return self._json(421, {"error": "wrong host"})
        u = urllib.parse.urlsplit(self.path); q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path.startswith("/api/"): return self._api("GET", u.path, q, None)
        rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        p = os.path.realpath(os.path.join(STATIC, rel))
        if not p.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(p): return self._json(404, {"error": "not found"})
        with open(p, "rb") as f: data = f.read()
        ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",): ctype += "; charset=utf-8"
        if p.endswith(".js"): ctype = "text/javascript; charset=utf-8"
        self._headers(200, ctype, len(data), {"Cache-Control": "no-cache"}); self.wfile.write(data)

    def do_POST(self):
        if not self._host_ok(): return self._json(421, {"error": "wrong host"})
        if self.headers.get("X-Codebot") != "1" or self.headers.get("Origin") not in self.server.origins:
            return self._json(403, {"error": "state-changing requests must come from the codebot page"})
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json": return self._json(415, {"error": "JSON only"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY: return self._json(413, {"error": "too large"})
        try: body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError: return self._json(400, {"error": "bad JSON"})
        if not isinstance(body, dict): return self._json(400, {"error": "a JSON object, please"})
        u = urllib.parse.urlsplit(self.path); self._api("POST", u.path, {}, body)

    def do_OPTIONS(self): self._json(405, {"error": "no cross-origin access"})
    def do_PUT(self): self._json(405, {"error": "method not allowed"})
    do_DELETE = do_PATCH = do_PUT

    def _api(self, method, path, q, body):
        try: self._json(200, api.dispatch(self.server.ctx, method, path, q, body))
        except api.ApiError as e: self._json(e.code, {"error": str(e)})
        except Exception as e:                                         # never leak a traceback (it can hold paths); keep it in the log
            log = os.path.join(self.server.ctx.home, "logs", "gui", "errors.log")
            try:
                os.makedirs(os.path.dirname(log), exist_ok=True)
                with open(log, "a") as f: f.write(f"{method} {path}\n{traceback.format_exc()}\n")
            except OSError: pass
            self._json(500, {"error": f"internal error ({type(e).__name__}); details in logs/gui/errors.log"})

def serve(home, port=8765, demo=False, open_browser=False):
    ctx = api.Ctx(home, demo=demo)
    srv = Server(port, ctx)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"codebot gui: {url}  (this machine only; Ctrl+C stops the GUI, never a run)", flush=True)
    if demo: print("DEMO MODE: the data comes from the fixture home given with --home; actions are disabled", flush=True)
    if open_browser:
        import webbrowser; webbrowser.open(url)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally: srv.server_close()
