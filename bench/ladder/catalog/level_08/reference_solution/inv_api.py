import json, re, sqlite3, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

MAX_BODY = 1_048_576
SKU = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
ROLES = ("admin", "reader")
SCHEMA = ["CREATE TABLE IF NOT EXISTS products(sku TEXT PRIMARY KEY, name TEXT NOT NULL, price_cents INTEGER NOT NULL, reorder_level INTEGER NOT NULL DEFAULT 0, stock INTEGER NOT NULL DEFAULT 0 CHECK(stock >= 0))",
          "CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL REFERENCES products(sku), delta INTEGER NOT NULL, reason TEXT NOT NULL, ts TEXT NOT NULL)"]

class ApiError(Exception):
    def __init__(self, status, error, **extra): self.status, self.body = status, {"error": error, **extra}

def is_int(v): return isinstance(v, int) and not isinstance(v, bool)

def validate_product(d, creating):
    fields, allowed = {}, ({"sku", "name", "price_cents", "reorder_level"} if creating else {"name", "price_cents", "reorder_level"})
    for k in d:
        if k not in allowed: fields[k] = "unknown field"
    if creating and not (isinstance(d.get("sku"), str) and SKU.match(d["sku"])): fields["sku"] = "must be 1-32 characters from A-Z a-z 0-9 . _ -"
    if (creating or "name" in d) and not (isinstance(d.get("name"), str) and d["name"].strip() and len(d["name"].strip()) <= 100): fields["name"] = "must be a non-empty string of at most 100 characters"
    if (creating or "price_cents" in d) and not (is_int(d.get("price_cents")) and d["price_cents"] >= 0): fields["price_cents"] = "must be a non-negative integer"
    if "reorder_level" in d and not (is_int(d["reorder_level"]) and d["reorder_level"] >= 0): fields["reorder_level"] = "must be a non-negative integer"
    if fields: raise ApiError(422, "validation failed", fields=fields)

def page(qs, default=20):
    def num(name, lo, hi, dflt):
        if name not in qs: return dflt
        v = qs[name][-1]
        if not re.fullmatch(r"[0-9]+", v) or not lo <= int(v) <= hi: raise ApiError(400, f"invalid {name}")
        return int(v)
    return num("limit", 1, 100, default), num("offset", 0, 10**9, 0)

class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, db_path, tokens, host, port):
        for t, r in tokens.items():
            if r not in ROLES or not isinstance(t, str) or not t: raise ValueError(f"invalid token or role: {r!r}")
        self.db_path, self.tokens, self._thread = db_path, dict(tokens), None
        c = self.connect()
        for s in SCHEMA: c.execute(s)
        c.close()
        super().__init__((host, port), Handler)
        self.port = self.server_address[1]

    def connect(self):
        c = sqlite3.connect(self.db_path, timeout=30, isolation_level=None); c.execute("PRAGMA foreign_keys = ON"); return c

    def start(self):
        self._thread = threading.Thread(target=self.serve_forever, daemon=True); self._thread.start(); return self

    def stop(self):
        if self._thread: self.shutdown(); self._thread.join(10); self._thread = None
        self.server_close()

    def __enter__(self): return self.start()
    def __exit__(self, *a): self.stop()

def make_server(db_path, tokens, host="127.0.0.1", port=0): return Server(db_path, tokens, host, port)

def row(r): return {"sku": r[0], "name": r[1], "price_cents": r[2], "reorder_level": r[3], "stock": r[4]}
COLS = "sku, name, price_cents, reorder_level, stock"

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def send(self, status, body=None, headers=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(data)

    def routes(self, path):
        parts = [p for p in path.split("/") if p]
        if parts == ["health"]: return "health", {}, {"GET"}
        if parts == ["products"]: return "products", {}, {"GET", "POST"}
        if len(parts) == 2 and parts[0] == "products": return "product", {"sku": parts[1]}, {"GET", "PATCH", "DELETE"}
        if len(parts) == 3 and parts[0] == "products" and parts[2] == "stock": return "stock", {"sku": parts[1]}, {"POST"}
        if len(parts) == 3 and parts[0] == "products" and parts[2] == "movements": return "movements", {"sku": parts[1]}, {"GET"}
        return None, {}, set()

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY: raise ApiError(413, "request too large")
        raw = self.rfile.read(n) if n else b""
        if not raw: raise ApiError(400, "invalid JSON")
        try: d = json.loads(raw.decode() or "null")
        except (ValueError, UnicodeDecodeError): raise ApiError(400, "invalid JSON")
        if not isinstance(d, dict): raise ApiError(400, "body must be a JSON object")
        return d

    def handle_any(self):
        db = None
        try:
            u = urlsplit(self.path); name, args, allowed = self.routes(u.path)
            if name is None: raise ApiError(404, "not found")
            if name != "health":
                h = self.headers.get("Authorization", ""); tok = h[7:] if h.startswith("Bearer ") else None
                if tok not in self.server.tokens: raise ApiError(401, "unauthorized")
                role = self.server.tokens[tok]
            if self.command not in allowed: raise ApiError(405, "method not allowed")
            if name != "health" and self.command != "GET" and role != "admin": raise ApiError(403, "forbidden")
            qs = parse_qs(u.query, keep_blank_values=True)
            if name == "health": return self.send(200, {"status": "ok"})
            d = self.body() if self.command in ("POST", "PATCH") else None
            db = self.server.connect()
            self.dispatch(db, name, args, qs, d)
        except ApiError as e:
            hdr = {"WWW-Authenticate": "Bearer"} if e.status == 401 else ({"Allow": ", ".join(sorted(allowed))} if e.status == 405 else {})
            if e.status == 413: self.close_connection = True
            self.send(e.status, e.body, hdr)
        finally:
            if db: db.close()

    do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = handle_any

    def product(self, db, sku):
        r = db.execute(f"SELECT {COLS} FROM products WHERE sku = ?", (sku,)).fetchone()
        if r is None: raise ApiError(404, "not found")
        return row(r)

    def dispatch(self, db, name, args, qs, d):
        c = self.command
        if name == "products" and c == "POST":
            validate_product(d, True); p = {"sku": d["sku"], "name": d["name"].strip(), "price_cents": d["price_cents"], "reorder_level": d.get("reorder_level", 0), "stock": 0}
            try: db.execute("INSERT INTO products(sku, name, price_cents, reorder_level) VALUES (?, ?, ?, ?)", (p["sku"], p["name"], p["price_cents"], p["reorder_level"]))
            except sqlite3.IntegrityError: raise ApiError(409, "sku already exists")
            return self.send(201, p, {"Location": f"/products/{p['sku']}"})
        if name == "products":
            limit, offset = page(qs); q = (qs.get("q") or [""])[-1].lower(); where, a = "", []
            if q: where = " WHERE lower(sku) LIKE ? OR lower(name) LIKE ?"; a = [f"%{q}%", f"%{q}%"]
            total = db.execute(f"SELECT COUNT(*) FROM products{where}", a).fetchone()[0]
            items = [row(r) for r in db.execute(f"SELECT {COLS} FROM products{where} ORDER BY sku LIMIT ? OFFSET ?", a + [limit, offset])]
            return self.send(200, {"items": items, "total": total, "limit": limit, "offset": offset})
        sku = args["sku"]
        if name == "product" and c == "GET": return self.send(200, self.product(db, sku))
        if name == "product" and c == "PATCH":
            if not d: raise ApiError(400, "nothing to update")
            validate_product(d, False); self.product(db, sku)
            sets = {k: (v.strip() if k == "name" else v) for k, v in d.items()}
            db.execute(f"UPDATE products SET {', '.join(k + ' = ?' for k in sets)} WHERE sku = ?", [*sets.values(), sku]); return self.send(200, self.product(db, sku))
        if name == "product" and c == "DELETE":
            db.execute("BEGIN IMMEDIATE")
            try:
                p = self.product(db, sku)
                if p["stock"] > 0: raise ApiError(409, "product still has stock")
                db.execute("DELETE FROM movements WHERE sku = ?", (sku,)); db.execute("DELETE FROM products WHERE sku = ?", (sku,)); db.execute("COMMIT")
            except BaseException: db.execute("ROLLBACK"); raise
            return self.send(204)
        if name == "stock":
            fields = {}
            if not (is_int(d.get("delta")) and d["delta"] != 0): fields["delta"] = "must be a non-zero integer"
            if not (isinstance(d.get("reason"), str) and d["reason"].strip()): fields["reason"] = "must be a non-empty string"
            for k in d:
                if k not in ("delta", "reason"): fields[k] = "unknown field"
            if fields: raise ApiError(422, "validation failed", fields=fields)
            db.execute("BEGIN IMMEDIATE")
            try:
                p = self.product(db, sku)
                if p["stock"] + d["delta"] < 0: raise ApiError(409, "insufficient stock")
                db.execute("UPDATE products SET stock = stock + ? WHERE sku = ?", (d["delta"], sku))
                mid = db.execute("INSERT INTO movements(sku, delta, reason, ts) VALUES (?, ?, ?, ?)", (sku, d["delta"], d["reason"].strip(), time.strftime("%Y-%m-%dT%H:%M:%S"))).lastrowid
                new = p["stock"] + d["delta"]; db.execute("COMMIT")
            except BaseException: db.execute("ROLLBACK"); raise
            return self.send(200, {"sku": sku, "stock": new, "movement_id": mid})
        if name == "movements":
            self.product(db, sku); limit, offset = page(qs)
            total = db.execute("SELECT COUNT(*) FROM movements WHERE sku = ?", (sku,)).fetchone()[0]
            items = [{"id": r[0], "delta": r[1], "reason": r[2], "ts": r[3]} for r in db.execute("SELECT id, delta, reason, ts FROM movements WHERE sku = ? ORDER BY id LIMIT ? OFFSET ?", (sku, limit, offset))]
            return self.send(200, {"items": items, "total": total, "limit": limit, "offset": offset})
