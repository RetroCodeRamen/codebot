"""Notes application, stage 4: a local HTTP API on top of the store."""
import argparse, json, os, re, shutil, sqlite3, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

class NotesError(Exception): pass
class ValidationError(NotesError, ValueError): pass
class NotFound(NotesError, KeyError): pass
class StoreError(NotesError): pass

def _now(): return time.strftime("%Y-%m-%dT%H:%M:%S")

def _title(v):
    if not isinstance(v, str) or not v.strip(): raise ValidationError("title must not be empty")
    v = v.strip()
    if len(v) > 100 or "\n" in v: raise ValidationError("title must be one line of at most 100 characters")
    return v

def _body(v):
    if not isinstance(v, str): raise ValidationError("body must be a string")
    if len(v) > 5000: raise ValidationError("body must be at most 5000 characters")
    return v

TAG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,19}$")

def _tags(v):
    if isinstance(v, str) or not hasattr(v, "__iter__"): raise ValidationError("tags must be a list of strings")
    out = []
    for t in v:
        if not isinstance(t, str): raise ValidationError("tags must be a list of strings")
        t = t.strip().lower()
        if not TAG.match(t): raise ValidationError(f"invalid tag '{t}'")
        if t not in out: out.append(t)
    if len(out) > 10: raise ValidationError("at most 10 tags are allowed")
    return out

SCHEMA = ["CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
          "CREATE TABLE notes(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL)",
          "CREATE TABLE note_tags(note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE, position INTEGER NOT NULL, tag TEXT NOT NULL, PRIMARY KEY(note_id, tag))"]
SCHEMA_VERSION = 1

def _read_legacy(path):
    """The (next_id, notes) of a stage 1/2 JSON file; StoreError when it is not one."""
    try:
        with open(path) as f: d = json.load(f)
        notes = [{"id": n["id"], "title": n["title"], "body": n["body"], "created": n["created"], "updated": n["updated"], "tags": list(n.get("tags", []))} for n in d["notes"]]
        if not all(isinstance(n["id"], int) and isinstance(n["title"], str) and isinstance(n["body"], str) for n in notes): raise ValueError("bad note")
        return int(d["next_id"]), notes
    except (ValueError, KeyError, TypeError, OSError, AttributeError) as e: raise StoreError(f"data file is corrupt: {path}") from e

def _create(db, next_id, notes):
    for sql in SCHEMA: db.execute(sql)
    db.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    for n in notes:
        db.execute("INSERT INTO notes(id, title, body, created, updated) VALUES (?, ?, ?, ?, ?)", (n["id"], n["title"], n["body"], n["created"], n["updated"]))
        for i, t in enumerate(n["tags"]): db.execute("INSERT INTO note_tags(note_id, position, tag) VALUES (?, ?, ?)", (n["id"], i, t))
    seq = max(next_id - 1, max((n["id"] for n in notes), default=0))
    if db.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'notes'", (seq,)).rowcount == 0: db.execute("INSERT INTO sqlite_sequence(name, seq) VALUES ('notes', ?)", (seq,))

class NoteStore:
    def __init__(self, path, clock=None):
        self.path, self.clock = path, clock or (lambda: os.environ.get("NOTES_NOW") or _now()); self._lock = threading.RLock()
        if not os.path.exists(path):                                   # a new database is built beside the path and linked in atomically, so nobody ever sees a half-made file
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".notes-"); os.close(fd); os.remove(tmp)
            db = sqlite3.connect(tmp, isolation_level=None)
            try: db.execute("BEGIN"); _create(db, 1, []); db.execute("COMMIT")
            finally: db.close()
            try: os.link(tmp, path)
            except FileExistsError: pass
            finally: os.remove(tmp)
        if True:
            with open(path, "rb") as f: head = f.read(16)
            if not head.startswith(b"SQLite format 3"):
                next_id, notes = _read_legacy(path)                              # a stage 1/2 JSON file: migrate it, keeping the original
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".notes-"); os.close(fd); os.remove(tmp)
                db = sqlite3.connect(tmp, isolation_level=None)
                try: db.execute("BEGIN"); _create(db, next_id, notes); db.execute("COMMIT")
                finally: db.close()
                shutil.copyfile(path, path + ".json.bak"); os.replace(tmp, path)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False); self.db.execute("PRAGMA foreign_keys = ON")
        try:
            have = self.db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if have is None: raise sqlite3.DatabaseError("no schema")
        except sqlite3.DatabaseError as e:
            if os.path.getsize(path) == 0 or "no such table" in str(e) or "no schema" in str(e) or "not a database" in str(e) or "malformed" in str(e):
                self.db.close(); raise StoreError(f"data file is corrupt: {path}") from e
            raise

    def schema_version(self): return int(self.db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0])

    def _tx(self, fn):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try: out = fn(); self.db.execute("COMMIT"); return out
            except BaseException: self.db.execute("ROLLBACK"); raise

    def _note(self, row):
        return {"id": row[0], "title": row[1], "body": row[2], "tags": [t[0] for t in self.db.execute("SELECT tag FROM note_tags WHERE note_id = ? ORDER BY position", (row[0],))], "created": row[3], "updated": row[4]}

    def _row(self, note_id):
        r = self.db.execute("SELECT id, title, body, created, updated FROM notes WHERE id = ?", (note_id,)).fetchone() if isinstance(note_id, int) and not isinstance(note_id, bool) else None
        if r is None: raise NotFound(note_id)
        return r

    def add(self, title, body="", tags=()):
        title, body, tags = _title(title), _body(body), _tags(tags); now = self.clock()
        def go():
            nid = self.db.execute("INSERT INTO notes(title, body, created, updated) VALUES (?, ?, ?, ?)", (title, body, now, now)).lastrowid
            for i, t in enumerate(tags): self.db.execute("INSERT INTO note_tags(note_id, position, tag) VALUES (?, ?, ?)", (nid, i, t))
            return self._note(self._row(nid))
        return self._tx(go)

    def get(self, note_id):
        with self._lock: return self._note(self._row(note_id))

    def list(self):
        with self._lock: return [self._note(r) for r in self.db.execute("SELECT id, title, body, created, updated FROM notes ORDER BY id").fetchall()]

    def update(self, note_id, title=None, body=None, tags=None):
        def go():
            r = self._row(note_id); t = _title(title) if title is not None else r[1]; b = _body(body) if body is not None else r[2]; tg = _tags(tags) if tags is not None else None
            self.db.execute("UPDATE notes SET title = ?, body = ?, updated = ? WHERE id = ?", (t, b, self.clock(), note_id))
            if tg is not None:
                self.db.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
                for i, x in enumerate(tg): self.db.execute("INSERT INTO note_tags(note_id, position, tag) VALUES (?, ?, ?)", (note_id, i, x))
            return self._note(self._row(note_id))
        return self._tx(go)

    def delete(self, note_id):
        def go():
            n = self._note(self._row(note_id)); self.db.execute("DELETE FROM notes WHERE id = ?", (note_id,)); return n
        return self._tx(go)

    def search(self, query="", tags=()):
        """Notes that contain every word of the query (title or body, ignoring case) and carry every given tag, ordered by id."""
        words, need = str(query).lower().split(), _tags(tags)
        return [n for n in self.list() if all(w in (n["title"] + "\n" + n["body"]).lower() for w in words) and all(t in n["tags"] for t in need)]

    def tags(self):
        """Every tag in use with the number of notes that have it, as sorted (tag, count) pairs."""
        with self._lock: return [(t, c) for t, c in self.db.execute("SELECT tag, COUNT(*) FROM note_tags GROUP BY tag ORDER BY tag")]

    def stats(self):
        with self._lock: return {"notes": self.db.execute("SELECT COUNT(*) FROM notes").fetchone()[0], "tags": self.db.execute("SELECT COUNT(DISTINCT tag) FROM note_tags").fetchone()[0]}

    def export_json(self, path):
        """Write the notes in the old JSON file format (for other tools); returns the number of notes."""
        with self._lock:
            notes = self.list(); seq = self.db.execute("SELECT seq FROM sqlite_sequence WHERE name = 'notes'").fetchone()
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".export-")
        with os.fdopen(fd, "w") as f: json.dump({"next_id": (seq[0] if seq else 0) + 1, "notes": notes}, f)
        os.replace(tmp, path); return len(notes)

    def close(self): self.db.close()

# ---------------- HTTP API ----------------
MAX_BODY = 1_048_576

class HttpError(Exception):
    def __init__(self, status, error, headers=None): self.status, self.error, self.headers = status, error, headers or {}

class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, store, host, port):
        self.store, self._thread = store, None; super().__init__((host, port), Handler); self.port = self.server_address[1]
    def start(self): self._thread = threading.Thread(target=self.serve_forever, daemon=True); self._thread.start(); return self
    def stop(self):
        if self._thread: self.shutdown(); self._thread.join(10); self._thread = None
        self.server_close()
    def __enter__(self): return self.start()
    def __exit__(self, *a): self.stop()

def make_server(store, host="127.0.0.1", port=0): return Server(store, host, port)

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

    ROUTES = [("GET", r"/health", "health"), ("GET", r"/notes", "list"), ("POST", r"/notes", "create"), ("GET", r"/notes/(\d+)", "get"), ("PUT", r"/notes/(\d+)", "put"), ("DELETE", r"/notes/(\d+)", "delete"), ("GET", r"/search", "search")]

    def handle_any(self):
        allowed = set()
        try:
            u = urlsplit(self.path); path = u.path.rstrip("/") or "/"; hit = None
            for m, pat, name in self.ROUTES:
                g = re.fullmatch(pat, path)
                if g:
                    allowed.add(m)
                    if m == self.command: hit = (name, [int(x) for x in g.groups()])
            if not allowed: raise HttpError(404, "not found")
            if hit is None: raise HttpError(405, "method not allowed", {"Allow": ", ".join(sorted(allowed))})
            qs = parse_qs(u.query, keep_blank_values=True); self.dispatch(hit[0], hit[1], qs, self.body() if self.command in ("POST", "PUT") else None)
        except HttpError as e: self.send(e.status, {"error": e.error}, e.headers)
        except NotFound as e: self.send(404, {"error": f"no note with id {e.args[0]}"})
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

    def dispatch(self, name, a, qs, d):
        s = self.server.store
        if name == "health": return self.send(200, {"status": "ok"})
        if name in ("list", "search"):
            limit, offset = self.paging(qs); tags = [t for v in qs.get("tag", []) for t in [v]]; items = s.search(qs.get("q", [""])[-1] if name == "search" else "", tags)
            return self.send(200, {"items": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset})
        if name == "create": n = s.add(d.get("title"), d.get("body", ""), d.get("tags", [])); return self.send(201, n, {"Location": f"/notes/{n['id']}"})
        if name == "get": return self.send(200, s.get(a[0]))
        if name == "put":
            if "title" not in d: raise ValidationError("title must not be empty")
            return self.send(200, s.update(a[0], d["title"], d.get("body", ""), d.get("tags", [])))
        if name == "delete": s.delete(a[0]); return self.send(204)


def line(n): return f"{n['id']:>3}  {n['title']}" + (("  " + " ".join("#" + t for t in n["tags"])) if n.get("tags") else "")

def main(argv=None, out=None):
    out = out or sys.stdout; say = lambda s="": print(s, file=out)
    ap = argparse.ArgumentParser(prog="notes"); sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("add"); a.add_argument("title"); a.add_argument("--body", default=""); a.add_argument("--tag", action="append", default=[])
    l = sub.add_parser("list"); l.add_argument("--tag", action="append", default=[]); sub.add_parser("show").add_argument("id", type=int); sub.add_parser("delete").add_argument("id", type=int)
    e = sub.add_parser("edit"); e.add_argument("id", type=int); e.add_argument("--title"); e.add_argument("--body"); e.add_argument("--tag", action="append")
    q = sub.add_parser("search"); q.add_argument("words", nargs="+"); q.add_argument("--tag", action="append", default=[]); sub.add_parser("tags"); sub.add_parser("stats"); sv = sub.add_parser("serve"); sv.add_argument("--host", default="127.0.0.1"); sv.add_argument("--port", type=int, default=8080)
    try:
        ns = ap.parse_args(argv)
        if not ns.cmd: ap.print_usage(sys.stderr); return 2
        s = NoteStore(os.environ.get("NOTES_FILE") or "notes.json")
        if ns.cmd == "add": say(f"Added note {s.add(ns.title, ns.body, ns.tag)['id']}")
        elif ns.cmd == "list": ns_ = s.search("", ns.tag); say("\n".join(line(n) for n in ns_) if ns_ else "No notes.")
        elif ns.cmd == "search": ns_ = s.search(" ".join(ns.words), ns.tag); say("\n".join(line(n) for n in ns_) if ns_ else "No matches.")
        elif ns.cmd == "serve":
            srv = make_server(s, ns.host, ns.port); say(f"Serving on http://{ns.host}:{srv.port}"); out.flush()
            try: srv.serve_forever()
            except KeyboardInterrupt: pass
            finally: srv.server_close()
        elif ns.cmd == "stats": st = s.stats(); say(f"Notes: {st['notes']}"); say(f"Tags: {st['tags']}")
        elif ns.cmd == "tags": ts = s.tags(); say("\n".join(f"{t}: {c}" for t, c in ts) if ts else "No tags.")
        elif ns.cmd == "show": n = s.get(ns.id); say(n["title"]); say(n["body"])
        elif ns.cmd == "delete": say(f"Deleted note {s.delete(ns.id)['id']}")
        elif ns.cmd == "edit":
            if ns.title is None and ns.body is None and ns.tag is None: print("Error: nothing to edit", file=sys.stderr); return 2
            say(f"Updated note {s.update(ns.id, ns.title, ns.body, ns.tag)['id']}")
        return 0
    except NotFound as e: print(f"Error: no note with id {e.args[0]}", file=sys.stderr); return 1
    except (ValidationError, StoreError) as e: print(f"Error: {e}", file=sys.stderr); return 2
    except SystemExit as e: return e.code if isinstance(e.code, int) else 2

if __name__ == "__main__": sys.exit(main())
