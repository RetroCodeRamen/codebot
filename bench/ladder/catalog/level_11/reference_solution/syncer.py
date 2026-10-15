import json, os, tempfile, threading
from dataclasses import dataclass, field
from typing import List, Optional

class ConnectionLost(ConnectionError):
    pass

KINDS = {"set": "register", "delete": "register", "incr": "counter"}

def _atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".sync-")
    with os.fdopen(fd, "w") as f:
        f.write(text); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def validate_op(op):
    """None if the operation is well formed, else a reason."""
    if not isinstance(op, dict): return "invalid operation"
    for k, t in (("op_id", str), ("device", str), ("ts", int), ("base", int), ("type", str), ("key", str)):
        if not isinstance(op.get(k), t) or isinstance(op.get(k), bool): return "invalid operation"
    if not op["op_id"] or not op["device"] or not op["key"] or len(op["key"]) > 200 or op["ts"] < 1 or op["base"] < 0: return "invalid operation"
    if op["type"] not in KINDS: return "invalid operation"
    if op["type"] == "incr" and (isinstance(op.get("value"), bool) or not isinstance(op.get("value"), int) or op["value"] == 0): return "invalid operation"
    if op["type"] == "delete" and op.get("value") is not None: return "invalid operation"
    if op["type"] == "set":
        try: json.dumps(op.get("value"))
        except (TypeError, ValueError): return "invalid operation"
    return None

def fold(ops):
    """The state defined by a SET of operations (order and duplicates do not matter): registers by highest (ts, device), counters by sum."""
    best, counters = {}, {}
    for op in {o["op_id"]: o for o in ops}.values():
        if op["type"] == "incr": counters[op["key"]] = counters.get(op["key"], 0) + op["value"]
        else:
            cur = best.get(op["key"])
            if cur is None or (op["ts"], op["device"]) > (cur["ts"], cur["device"]): best[op["key"]] = op
    state = {k: o["value"] for k, o in best.items() if o["type"] == "set"}
    state.update(counters)
    return dict(sorted(state.items()))

class SyncServer:
    def __init__(self, path=None):
        self.path, self.log, self.ids, self.kinds = path, [], set(), {}
        self._lock = threading.RLock()
        if path and os.path.exists(path):
            with open(path) as f:
                for ln in f:
                    if ln.strip():
                        try: op = json.loads(ln)
                        except ValueError: break                      # a torn last line from a crash: everything before it is intact
                        self._add(op)

    def _add(self, op):
        self.log.append(op); self.ids.add(op["op_id"]); self.kinds.setdefault(op["key"], KINDS[op["type"]])

    def latest_seq(self): return len(self.log)

    def push(self, device_id, ops):
        with self._lock:
            acc, dup, rej, fresh = [], [], [], []
            for op in ops:
                why = validate_op(op)
                if why is None and op["device"] != device_id: why = "invalid operation"
                if why: rej.append({"op_id": op.get("op_id") if isinstance(op, dict) else None, "reason": why}); continue
                if op["op_id"] in self.ids or any(o["op_id"] == op["op_id"] for o in fresh): dup.append(op["op_id"]); continue
                kind = self.kinds.get(op["key"]) or next((KINDS[o["type"]] for o in fresh if o["key"] == op["key"]), None)
                if kind is not None and kind != KINDS[op["type"]]: rej.append({"op_id": op["op_id"], "reason": "type mismatch"}); continue
                fresh.append({k: op[k] for k in ("op_id", "device", "ts", "base", "type", "key", "value") if k in op} | ({"value": None} if "value" not in op else {})); acc.append(op["op_id"])
            if fresh:
                for op in fresh: op["seq"] = len(self.log) + 1; self._add(op)
                if self.path:
                    with open(self.path, "a") as f:
                        f.write("".join(json.dumps(o) + "\n" for o in fresh)); f.flush(); os.fsync(f.fileno())
            return {"accepted": acc, "duplicates": dup, "rejected": rej, "latest_seq": len(self.log)}

    def pull(self, since, limit=100):
        with self._lock:
            ops = [dict(o) for o in self.log[since:since + limit]]
            return {"ops": ops, "latest_seq": len(self.log), "more": since + limit < len(self.log)}

    def snapshot(self):
        with self._lock: return fold(self.log)

class Link:
    """The connection between a device and the server. Failures can be injected: `fail_next(n, where)` makes the next n calls raise ConnectionLost,
    either before the server saw the request ("request") or after the server handled it but before the answer came back ("response")."""
    def __init__(self, server):
        self.server, self.calls, self._fail, self.where, self.log = server, 0, 0, "request", []

    def fail_next(self, n=1, where="request"):
        if where not in ("request", "response"): raise ValueError("where must be 'request' or 'response'")
        self._fail, self.where = n, where

    def _call(self, name, *args):
        self.calls += 1; self.log.append(name)
        if self._fail and self.where == "request": self._fail -= 1; raise ConnectionLost(f"{name}: request lost")
        out = getattr(self.server, name)(*args)
        if self._fail and self.where == "response": self._fail -= 1; raise ConnectionLost(f"{name}: response lost")
        return out

    def push(self, device_id, ops): return self._call("push", device_id, ops)
    def pull(self, since, limit=100): return self._call("pull", since, limit)

@dataclass
class SyncReport:
    completed: bool = False
    pushed: int = 0
    pulled: int = 0
    duplicates: int = 0
    rejected: List[dict] = field(default_factory=list)
    conflicts: int = 0
    error: Optional[str] = None

class Device:
    def __init__(self, device_id, link, state_path=None):
        if not isinstance(device_id, str) or not device_id.strip() or "/" in device_id: raise ValueError("device_id must be a non-empty string without '/'")
        self.device_id, self.link, self.state_path = device_id, link, state_path
        self.lamport, self.counter, self.last_seq, self.outbox, self.known, self.conflicts = 0, 0, 0, [], {}, []
        if state_path and os.path.exists(state_path): self._load()

    # ---- persistence ----
    def _save(self):
        if self.state_path:
            _atomic_write(self.state_path, json.dumps({"device_id": self.device_id, "lamport": self.lamport, "counter": self.counter, "last_seq": self.last_seq, "outbox": self.outbox,
                                                      "known": list(self.known.values()), "conflicts": self.conflicts}))

    def _load(self):
        with open(self.state_path) as f: d = json.load(f)
        if d["device_id"] != self.device_id: raise ValueError(f"state file belongs to device {d['device_id']!r}")
        self.lamport, self.counter, self.last_seq, self.outbox, self.conflicts = d["lamport"], d["counter"], d["last_seq"], d["outbox"], d["conflicts"]
        self.known = {o["op_id"]: o for o in d["known"]}

    # ---- local operations ----
    def _op(self, type_, key, value):
        if not isinstance(key, str) or not key or len(key) > 200: raise ValueError("key must be a non-empty string of at most 200 characters")
        if type_ == "set":
            try: json.dumps(value)
            except (TypeError, ValueError): raise ValueError("value must be JSON-serializable")
        if type_ == "incr" and (isinstance(value, bool) or not isinstance(value, int) or value == 0): raise ValueError("increment must be a non-zero integer")
        kind = KINDS[type_]; seen = self._kind_of(key)
        if seen is not None and seen != kind: raise ValueError(f"key '{key}' is a {seen}, not a {kind}")
        self.lamport += 1; self.counter += 1
        op = {"op_id": f"{self.device_id}-{self.counter}", "device": self.device_id, "ts": self.lamport, "base": self.last_seq, "type": type_, "key": key, "value": value}
        self.outbox.append(op); self._save(); return op["op_id"]

    def _kind_of(self, key):
        for o in list(self.known.values()) + self.outbox:
            if o["key"] == key: return KINDS[o["type"]]
        return None

    def set(self, key, value): return self._op("set", key, value)
    def delete(self, key): return self._op("delete", key, None)
    def increment(self, key, n=1): return self._op("incr", key, n)

    def _view(self): return fold(list(self.known.values()) + self.outbox)
    def get(self, key, default=None): return self._view().get(key, default)
    def items(self): return self._view()
    def pending(self): return [dict(o) for o in self.outbox]

    # ---- synchronisation ----
    def _note_conflicts(self, op):
        """A remote operation on a register key is a conflict with each of our own operations on that key that neither saw it nor was seen by it."""
        if op["type"] == "incr" or op["device"] == self.device_id: return 0
        mine = [o for o in list(self.known.values()) + self.outbox if o["key"] == op["key"] and o["device"] == self.device_id]; n = 0
        for y in mine:
            y_seq = y.get("seq", float("inf"))
            if y["base"] < op["seq"] and op["base"] < y_seq:
                key_ops = [o for o in list(self.known.values()) + self.outbox + [op] if o["key"] == op["key"] and o["type"] != "incr"]
                win = max(key_ops, key=lambda o: (o["ts"], o["device"]))
                self.conflicts.append({"key": op["key"], "local_op": y["op_id"], "remote_op": op["op_id"], "winner_op": win["op_id"], "value": win["value"] if win["type"] == "set" else None}); n += 1
        return n

    def sync(self, batch=50, page=50):
        rep = SyncReport()
        try:
            while self.outbox:
                chunk = [dict(o) for o in self.outbox[:batch]]; ans = self.link.push(self.device_id, chunk)
                done = set(ans["accepted"]) | set(ans["duplicates"]); bad = {r["op_id"] for r in ans["rejected"]}
                rep.pushed += len(ans["accepted"]); rep.duplicates += len(ans["duplicates"]); rep.rejected += ans["rejected"]
                for o in chunk:
                    if o["op_id"] in done: self.known[o["op_id"]] = o
                self.outbox = [o for o in self.outbox if o["op_id"] not in done and o["op_id"] not in bad]; self._save()
            while True:
                ans = self.link.pull(self.last_seq, page)
                for op in ans["ops"]:
                    rep.conflicts += self._note_conflicts(op); self.known[op["op_id"]] = op; self.lamport = max(self.lamport, op["ts"])
                    if op["device"] == self.device_id: self.counter = max(self.counter, int(op["op_id"].rsplit("-", 1)[1]))
                rep.pulled += len(ans["ops"])
                if ans["ops"]: self.last_seq = ans["ops"][-1]["seq"]
                self._save()
                if not ans["more"]: break
            rep.completed = True
        except ConnectionError as e: rep.error = str(e)
        return rep

    def clear_conflicts(self): n = len(self.conflicts); self.conflicts = []; self._save(); return n
