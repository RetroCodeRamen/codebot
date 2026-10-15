import functools, sqlite3, threading, time
from dataclasses import dataclass
from typing import Optional

class InventoryError(Exception): pass
class DuplicateSku(InventoryError): pass
class UnknownSku(InventoryError): pass
class UnknownOrder(InventoryError): pass
class InsufficientStock(InventoryError): pass
class SchemaError(InventoryError): pass

@dataclass
class Product:
    sku: str
    name: str
    price_cents: int
    stock: int
    reorder_level: int

@dataclass
class Movement:
    id: int
    sku: str
    delta: int
    reason: str
    ref: Optional[str]
    ts: str

@dataclass
class Reservation:
    id: int
    order_ref: str
    sku: str
    qty: int
    state: str
    ts: str

MIGRATIONS = {
    1: ["CREATE TABLE products(sku TEXT PRIMARY KEY, name TEXT NOT NULL, price_cents INTEGER NOT NULL CHECK(price_cents >= 0), stock INTEGER NOT NULL DEFAULT 0 CHECK(stock >= 0))",
        "CREATE TABLE movements(id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL REFERENCES products(sku), delta INTEGER NOT NULL, reason TEXT NOT NULL, ref TEXT, ts TEXT NOT NULL)"],
    2: ["CREATE TABLE reservations(id INTEGER PRIMARY KEY AUTOINCREMENT, order_ref TEXT NOT NULL, sku TEXT NOT NULL REFERENCES products(sku), qty INTEGER NOT NULL CHECK(qty > 0), "
        "state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active', 'released', 'fulfilled')), ts TEXT NOT NULL)"],
    3: ["ALTER TABLE products ADD COLUMN reorder_level INTEGER NOT NULL DEFAULT 0 CHECK(reorder_level >= 0)", "CREATE INDEX movements_sku ON movements(sku)"],
}
LATEST = max(MIGRATIONS)

def _int(v, name, minimum=None):
    if isinstance(v, bool) or not isinstance(v, int): raise ValueError(f"{name} must be an integer")
    if minimum is not None and v < minimum: raise ValueError(f"{name} must be at least {minimum}")
    return v

def _text(v, name):
    if not isinstance(v, str) or not v.strip(): raise ValueError(f"{name} must be a non-empty string")
    return v.strip()

def _locked(fn):
    @functools.wraps(fn)
    def w(self, *a, **k):
        with self._lock: return fn(self, *a, **k)
    return w

class Inventory:
    SCHEMA_VERSION = LATEST

    def __init__(self, path=":memory:", clock=None):
        self._lock = threading.RLock()
        self.clock = clock or (lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    @_locked
    def schema_version(self): return self.db.execute("PRAGMA user_version").fetchone()[0]

    def _migrate(self):
        v = self.schema_version()
        if v > LATEST: raise SchemaError(f"database schema version {v} is newer than this program supports ({LATEST})")
        for n in range(v + 1, LATEST + 1):
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if self.schema_version() >= n: self.db.execute("COMMIT"); continue          # another process migrated meanwhile
                for sql in MIGRATIONS[n]: self.db.execute(sql)
                self.db.execute(f"PRAGMA user_version = {n}"); self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK"); raise

    # ---- transaction helper ----
    @_locked
    def _tx(self, fn):
        self.db.execute("BEGIN IMMEDIATE")
        try: out = fn(); self.db.execute("COMMIT"); return out
        except BaseException:
            self.db.execute("ROLLBACK"); raise

    def _product(self, sku):
        r = self.db.execute("SELECT sku, name, price_cents, stock, reorder_level FROM products WHERE sku = ?", (sku,)).fetchone()
        if r is None: raise UnknownSku(sku)
        return Product(*r)

    def _reserved(self, sku):
        return self.db.execute("SELECT COALESCE(SUM(qty), 0) FROM reservations WHERE sku = ? AND state = 'active'", (sku,)).fetchone()[0]

    def _move(self, sku, delta, reason, ref=None):
        p = self._product(sku)
        if p.stock + delta < 0: raise InsufficientStock(f"{sku}: stock {p.stock}, change {delta}")
        if delta < 0 and p.stock + delta < self._reserved(sku): raise InsufficientStock(f"{sku}: {self._reserved(sku)} reserved, only {p.stock + delta} would remain")
        self.db.execute("UPDATE products SET stock = stock + ? WHERE sku = ?", (delta, sku))
        ts = self.clock()
        cur = self.db.execute("INSERT INTO movements(sku, delta, reason, ref, ts) VALUES (?, ?, ?, ?, ?)", (sku, delta, reason, ref, ts))
        return Movement(cur.lastrowid, sku, delta, reason, ref, ts)

    # ---- products ----
    def add_product(self, sku, name, price_cents, reorder_level=0):
        return self._tx(lambda: self._add_product(sku, name, price_cents, reorder_level))

    def _add_product(self, sku, name, price_cents, reorder_level):
        sku, name = _text(sku, "sku"), _text(name, "name"); price_cents = _int(price_cents, "price_cents", 0); reorder_level = _int(reorder_level, "reorder_level", 0)
        if self.db.execute("SELECT 1 FROM products WHERE sku = ?", (sku,)).fetchone(): raise DuplicateSku(sku)
        self.db.execute("INSERT INTO products(sku, name, price_cents, stock, reorder_level) VALUES (?, ?, ?, 0, ?)", (sku, name, price_cents, reorder_level)); return self._product(sku)

    @_locked
    def get_product(self, sku): return self._product(sku)
    @_locked
    def list_products(self): return [Product(*r) for r in self.db.execute("SELECT sku, name, price_cents, stock, reorder_level FROM products ORDER BY sku")]

    # ---- stock ----
    def receive(self, sku, qty, ref=None): return self._tx(lambda: self._receive(sku, qty, ref))
    def _receive(self, sku, qty, ref): _int(qty, "qty", 1); self._move(sku, qty, "receive", ref); return self._product(sku).stock

    def adjust(self, sku, delta, reason): return self._tx(lambda: self._adjust(sku, delta, reason))
    def _adjust(self, sku, delta, reason):
        _int(delta, "delta"); _text(reason, "reason")
        if delta == 0: raise ValueError("delta must not be zero")
        self._move(sku, delta, reason.strip()); return self._product(sku).stock

    @_locked
    def stock(self, sku): return self._product(sku).stock
    @_locked
    def available(self, sku): return self._product(sku).stock - self._reserved(sku)
    @_locked
    def history(self, sku):
        self._product(sku); return [Movement(*r) for r in self.db.execute("SELECT id, sku, delta, reason, ref, ts FROM movements WHERE sku = ? ORDER BY id", (sku,))]

    # ---- reservations ----
    def reserve(self, order_ref, sku, qty): return self._tx(lambda: self._reserve(order_ref, sku, qty))
    def _reserve(self, order_ref, sku, qty):
        order_ref = _text(order_ref, "order_ref"); _int(qty, "qty", 1); p = self._product(sku); free = p.stock - self._reserved(sku)
        if qty > free: raise InsufficientStock(f"{sku}: {free} available, {qty} requested")
        cur = self.db.execute("INSERT INTO reservations(order_ref, sku, qty, state, ts) VALUES (?, ?, ?, 'active', ?)", (order_ref, sku, qty, self.clock()))
        return self._reservation(cur.lastrowid)

    def _reservation(self, rid): return Reservation(*self.db.execute("SELECT id, order_ref, sku, qty, state, ts FROM reservations WHERE id = ?", (rid,)).fetchone())

    @_locked
    def reservations(self, order_ref=None, state=None):
        q, a = "SELECT id, order_ref, sku, qty, state, ts FROM reservations WHERE 1=1", []
        if order_ref is not None: q += " AND order_ref = ?"; a.append(order_ref)
        if state is not None: q += " AND state = ?"; a.append(state)
        return [Reservation(*r) for r in self.db.execute(q + " ORDER BY id", a)]

    def release(self, order_ref): return self._tx(lambda: self._close_order(order_ref, "released"))
    def fulfill(self, order_ref): return self._tx(lambda: self._close_order(order_ref, "fulfilled"))

    def _close_order(self, order_ref, state):
        rows = self.db.execute("SELECT id, sku, qty FROM reservations WHERE order_ref = ? AND state = 'active' ORDER BY id", (order_ref,)).fetchall()
        if not rows: raise UnknownOrder(order_ref)
        moves = []
        for rid, sku, qty in rows:
            self.db.execute("UPDATE reservations SET state = ? WHERE id = ?", (state, rid))
            if state == "fulfilled": moves.append(self._move(sku, -qty, "sale", order_ref))
        return moves if state == "fulfilled" else len(rows)

    # ---- batches ----
    def batch(self, ops): return self._tx(lambda: [self._op(op) for op in ops])

    def _op(self, op):
        name, args = op[0], op[1:]
        table = {"add_product": self._add_product, "receive": lambda s, q, r=None: self._receive(s, q, r), "adjust": self._adjust, "reserve": self._reserve,
                 "release": lambda o: self._close_order(o, "released"), "fulfill": lambda o: self._close_order(o, "fulfilled")}
        if name not in table: raise ValueError(f"unknown operation '{name}'")
        if name == "add_product" and len(args) == 3: args = args + (0,)
        return table[name](*args)

    # ---- consistency and reports ----
    @_locked
    def verify(self):
        out = []
        for sku, cached, real in self.db.execute("SELECT p.sku, p.stock, COALESCE((SELECT SUM(delta) FROM movements m WHERE m.sku = p.sku), 0) FROM products p ORDER BY p.sku"):
            if cached != real: out.append(f"stock mismatch for {sku}: cached {cached}, movements {real}")
        for sku, stock, reserved in self.db.execute("SELECT p.sku, p.stock, COALESCE((SELECT SUM(qty) FROM reservations r WHERE r.sku = p.sku AND r.state = 'active'), 0) FROM products p ORDER BY p.sku"):
            if reserved > stock: out.append(f"over-reserved {sku}: reserved {reserved}, stock {stock}")
        return out

    def repair(self):
        def go():
            n = 0
            for sku, cached, real in self.db.execute("SELECT p.sku, p.stock, COALESCE((SELECT SUM(delta) FROM movements m WHERE m.sku = p.sku), 0) FROM products p").fetchall():
                if cached != real: self.db.execute("UPDATE products SET stock = ? WHERE sku = ?", (real, sku)); n += 1
            return n
        return self._tx(go)

    @_locked
    def low_stock(self):
        return [r[0] for r in self.db.execute("SELECT p.sku FROM products p WHERE p.reorder_level > 0 AND p.stock - COALESCE((SELECT SUM(qty) FROM reservations r WHERE r.sku = p.sku AND r.state = 'active'), 0) <= p.reorder_level ORDER BY p.sku")]

    @_locked
    def stock_value(self): return self.db.execute("SELECT COALESCE(SUM(stock * price_cents), 0) FROM products").fetchone()[0]
