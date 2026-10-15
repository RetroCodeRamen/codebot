"""The HTTP API."""
import json, re, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
from .errors import AuthError, NotFound, PermissionDenied, StoreError, ValidationError

MAX_BODY = 1_048_576

class HttpError(Exception):
    def __init__(self, status, error, headers=None): self.status, self.error, self.headers = status, error, headers or {}

class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, store, host, port, auth):
        self.store, self.auth, self._thread = store, auth, None; super().__init__((host, port), Handler); self.port = self.server_address[1]
    def start(self): self._thread = threading.Thread(target=self.serve_forever, daemon=True); self._thread.start(); return self
    def stop(self):
        if self._thread: self.shutdown(); self._thread.join(10); self._thread = None
        self.server_close()
    def __enter__(self): return self.start()
    def __exit__(self, *a): self.stop()

def make_server(store, host="127.0.0.1", port=0, auth=False): return Server(store, host, port, auth)

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def send(self, status, body=None, headers=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.end_headers()
        if data: self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY: self.close_connection = True; raise HttpError(413, "request too large")
        raw = self.rfile.read(n) if n else b""
        try: d = json.loads(raw.decode()) if raw else None
        except (ValueError, UnicodeDecodeError): raise HttpError(400, "invalid JSON")
        if not isinstance(d, dict): raise HttpError(400, "body must be a JSON object")
        return d

    ROUTES = [("GET", r"/health", "health"), ("GET", r"/notes", "list"), ("POST", r"/notes", "create"), ("GET", r"/notes/(\d+)", "get"), ("PUT", r"/notes/(\d+)", "put"), ("DELETE", r"/notes/(\d+)", "delete"), ("GET", r"/search", "search"), ("PUT", r"/notes/(\d+)/reminder", "remind"), ("DELETE", r"/notes/(\d+)/reminder", "unremind"), ("GET", r"/notifications", "notifications"), ("POST", r"/notifications/read", "read")]
    SYNC_ROUTES = [("GET", r"/sync/snapshot", "snapshot"), ("POST", r"/sync/apply", "apply")]
    AUTH_ROUTES = [("POST", r"/register", "register"), ("POST", r"/login", "login"), ("POST", r"/logout", "logout"), ("GET", r"/notes/(\d+)/shares", "shares"), ("POST", r"/notes/(\d+)/shares", "share"), ("DELETE", r"/notes/(\d+)/shares/([^/]+)", "unshare")]

    def handle_any(self):
        allowed = set()
        try:
            u = urlsplit(self.path); path = u.path.rstrip("/") or "/"; hit = None
            for m, pat, name in self.ROUTES + (self.AUTH_ROUTES if self.server.auth else self.SYNC_ROUTES):
                g = re.fullmatch(pat, path)
                if g:
                    allowed.add(m)
                    if m == self.command: hit = (name, [int(x) if x.isdigit() else unquote(x) for x in g.groups()])
            if not allowed: raise HttpError(404, "not found")
            if hit is None: raise HttpError(405, "method not allowed", {"Allow": ", ".join(sorted(allowed))})
            name, a = hit; s = self.server.store; actor = tok = None
            if self.server.auth and name not in ("health", "register", "login"):
                h = self.headers.get("Authorization", ""); tok = h[7:] if h.startswith("Bearer ") else None
                try: actor = s.user_for_token(tok)
                except AuthError: raise HttpError(401, "unauthorized", {"WWW-Authenticate": "Bearer"})
            qs = parse_qs(u.query, keep_blank_values=True)
            d = self.body() if self.command in ("POST", "PUT") and name != "logout" else None
            self.dispatch(name, a, qs, d, actor, tok)
        except HttpError as e: self.send(e.status, {"error": e.error}, e.headers)
        except AuthError as e: self.send(401, {"error": str(e)})
        except PermissionDenied as e: self.send(403, {"error": str(e)})
        except NotFound as e: self.send(404, {"error": f"no note with id {e.args[0]}" if isinstance(e.args[0], int) else f"not found: {e.args[0]}"})
        except ValidationError as e: self.send(422, {"error": str(e)})
        except StoreError as e: self.send(500, {"error": str(e)})

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = handle_any

    def paging(self, qs):
        def num(k, lo, hi, d):
            if k not in qs: return d
            v = qs[k][-1]
            if not re.fullmatch(r"[0-9]+", v) or not lo <= int(v) <= hi: raise HttpError(400, f"invalid {k}")
            return int(v)
        return num("limit", 1, 100, 20), num("offset", 0, 10**9, 0)

    def dispatch(self, name, a, qs, d, actor, tok):
        s = self.server.store
        if name == "health": return self.send(200, {"status": "ok"})
        if name == "register": return self.send(201, s.create_user(d.get("name"), d.get("password")))
        if name == "login": return self.send(200, {"token": s.login(d.get("name"), d.get("password"))})
        if name == "logout": s.logout(tok); return self.send(204)
        if name in ("list", "search"):
            limit, offset = self.paging(qs); items = s.search(qs.get("q", [""])[-1] if name == "search" else "", qs.get("tag", []), actor)
            return self.send(200, {"items": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset})
        if name == "create": n = s.add(d.get("title"), d.get("body", ""), d.get("tags", []), actor); return self.send(201, n, {"Location": f"/notes/{n['id']}"})
        if name == "get": return self.send(200, s.get(a[0], actor))
        if name == "put":
            if "title" not in d: raise ValidationError("title must not be empty")
            return self.send(200, s.update(a[0], d["title"], d.get("body", ""), d.get("tags", []), actor))
        if name == "snapshot": return self.send(200, s.snapshot())
        if name == "apply": return self.send(200, s.apply_snapshot(d))
        if name == "delete": s.delete(a[0], actor); return self.send(204)
        if name == "remind": return self.send(200, s.set_reminder(a[0], d.get("due"), actor))
        if name == "unremind": return self.send(200, s.clear_reminder(a[0], actor))
        if name == "notifications": items = s.notifications(actor, qs.get("unread", ["0"])[-1] in ("1", "true")); return self.send(200, {"items": items, "total": len(items)})
        if name == "read":
            if not isinstance(d.get("ids"), list): raise ValidationError("ids must be a list of notification ids")
            return self.send(200, {"marked": s.mark_read(d["ids"], actor)})
        if name == "shares": return self.send(200, {"items": s.shares(a[0], actor)})
        if name == "share": return self.send(200, {"items": s.share(a[0], d.get("user"), d.get("mode"), actor)})
        if name == "unshare": s.unshare(a[0], a[1], actor); return self.send(204)
