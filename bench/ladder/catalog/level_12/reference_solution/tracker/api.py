"""The local HTTP+JSON API of the tracker (see make_server)."""
import json, re, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
from .core import AuthError, Conflict, NotFound, PermissionDenied, TrackerError, ValidationError

MAX_BODY = 1_048_576
STATUS = {AuthError: 401, PermissionDenied: 403, NotFound: 404, Conflict: 409, ValidationError: 422}

class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, tracker, host, port): self.tracker, self._thread = tracker, None; super().__init__((host, port), Handler); self.port = self.server_address[1]
    def start(self): self._thread = threading.Thread(target=self.serve_forever, daemon=True); self._thread.start(); return self
    def stop(self):
        if self._thread: self.shutdown(); self._thread.join(10); self._thread = None
        self.server_close()
    def __enter__(self): return self.start()
    def __exit__(self, *a): self.stop()

def make_server(tracker, host="127.0.0.1", port=0): return Server(tracker, host, port)

class HttpError(Exception):
    def __init__(self, status, error, headers=None): self.status, self.error, self.headers = status, error, headers or {}

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def send(self, status, body=None, headers=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY: self.close_connection = True; raise HttpError(413, "request too large")
        raw = self.rfile.read(n) if n else b""
        try: d = json.loads(raw.decode()) if raw else None
        except (ValueError, UnicodeDecodeError): raise HttpError(400, "invalid JSON")
        if not isinstance(d, dict): raise HttpError(400, "body must be a JSON object")
        return d

    ROUTES = [("POST", r"/login", "login"), ("POST", r"/logout", "logout"), ("GET", r"/me", "me"), ("GET", r"/projects", "projects"), ("POST", r"/projects", "project_create"), ("GET", r"/projects/([A-Z0-9]+)", "project"),
              ("POST", r"/projects/([A-Z0-9]+)/members", "member_add"), ("DELETE", r"/projects/([A-Z0-9]+)/members/([^/]+)", "member_remove"), ("GET", r"/projects/([A-Z0-9]+)/stats", "stats"), ("GET", r"/projects/([A-Z0-9]+)/export", "export"),
              ("POST", r"/projects/([A-Z0-9]+)/issues", "issue_create"), ("GET", r"/issues", "search"), ("GET", r"/issues/([A-Z0-9]+-\d+)", "issue"), ("PATCH", r"/issues/([A-Z0-9]+-\d+)", "issue_update"),
              ("POST", r"/issues/([A-Z0-9]+-\d+)/transition", "transition"), ("POST", r"/issues/([A-Z0-9]+-\d+)/comments", "comment_add"), ("POST", r"/issues/([A-Z0-9]+-\d+)/labels", "label_add"),
              ("DELETE", r"/issues/([A-Z0-9]+-\d+)/labels/([^/]+)", "label_remove"), ("PATCH", r"/comments/(\d+)", "comment_edit"), ("DELETE", r"/comments/(\d+)", "comment_delete"), ("GET", r"/audit", "audit"), ("GET", r"/health", "health")]

    def route(self, path):
        allowed, hit = set(), None
        for m, pat, name in self.ROUTES:
            g = re.fullmatch(pat, path)
            if g:
                allowed.add(m)
                if m == self.command: hit = (name, [unquote(x) for x in g.groups()])
        return hit, allowed

    def handle_any(self):
        allowed = set()
        try:
            u = urlsplit(self.path); hit, allowed = self.route(u.path.rstrip("/") or "/")
            if not allowed: raise HttpError(404, "not found")
            if hit is None: raise HttpError(405, "method not allowed", {"Allow": ", ".join(sorted(allowed))})
            name, args = hit; t = self.server.tracker; qs = {k: v[-1] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
            if name == "health": return self.send(200, {"status": "ok"})
            if name == "login": d = self.body(); return self.send(200, {"token": t.login(str(d.get("name", "")), str(d.get("password", "")))})
            h = self.headers.get("Authorization", ""); tok = h[7:] if h.startswith("Bearer ") else None
            try: me = t.authenticate_token(tok)
            except AuthError: raise HttpError(401, "unauthorized", {"WWW-Authenticate": "Bearer"})
            d = self.body() if self.command in ("POST", "PATCH") and name != "logout" else None
            who = me["name"]; self.dispatch(t, name, args, qs, d, who, tok)
        except HttpError as e: self.send(e.status, {"error": e.error}, e.headers)
        except TrackerError as e:
            status = next((s for c, s in STATUS.items() if isinstance(e, c)), 400); self.send(status, {"error": str(e)})

    do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = handle_any

    def dispatch(self, t, name, a, qs, d, who, tok):
        def num(k, dflt):
            if k not in qs: return dflt
            if not re.fullmatch(r"[0-9]+", qs[k]): raise HttpError(400, f"invalid {k}")
            return int(qs[k])
        if name == "logout": t.logout(tok); return self.send(204)
        if name == "me": return self.send(200, t.get_user(who))
        if name == "projects": return self.send(200, {"items": t.list_projects(who)})
        if name == "project_create": p = t.create_project(d.get("key"), d.get("name"), who, bool(d.get("public", False))); return self.send(201, p, {"Location": f"/projects/{p['key']}"})
        if name == "project": return self.send(200, t.get_project(a[0], who))
        if name == "member_add": return self.send(200, t.add_member(a[0], d.get("user"), d.get("role"), who))
        if name == "member_remove": t.remove_member(a[0], a[1], who); return self.send(204)
        if name == "stats": return self.send(200, t.project_stats(a[0], who))
        if name == "export": return self.send(200, t.export_project(a[0], who))
        if name == "issue_create":
            i = t.create_issue(a[0], d.get("title"), d.get("description", ""), who, d.get("priority", "medium"), d.get("assignee"), d.get("labels", [])); return self.send(201, i, {"Location": f"/issues/{i['key']}"})
        if name == "search":
            limit, offset = num("limit", 20), num("offset", 0)
            if not 1 <= limit <= 100: raise HttpError(400, "invalid limit")
            return self.send(200, t.search(qs.get("q", ""), who, limit, offset, qs.get("sort", "-updated")))
        if name == "issue": return self.send(200, t.get_issue(a[0], who))
        if name == "issue_update": return self.send(200, t.update_issue(a[0], who, **d))
        if name == "transition": return self.send(200, t.transition(a[0], d.get("status"), who, d.get("resolution")))
        if name == "comment_add": return self.send(201, t.add_comment(a[0], d.get("body"), who))
        if name == "label_add": return self.send(200, t.add_label(a[0], d.get("label"), who))
        if name == "label_remove": return self.send(200, t.remove_label(a[0], a[1], who))
        if name == "comment_edit": return self.send(200, t.edit_comment(int(a[0]), d.get("body"), who))
        if name == "comment_delete": t.delete_comment(int(a[0]), who); return self.send(204)
        if name == "audit":
            es = t.audit_entries(who, action=qs.get("action"), entity=qs.get("entity"), entity_id=qs.get("id"), who=qs.get("actor"), outcome=qs.get("outcome"), limit=num("limit", 0) or None); return self.send(200, {"items": es, "total": len(es)})
