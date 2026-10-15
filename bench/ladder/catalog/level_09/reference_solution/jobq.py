import functools, json, sqlite3, threading, time, uuid
from dataclasses import dataclass
from typing import Any, Optional

STATES = ("queued", "running", "succeeded", "failed", "cancelled")

class JobNotFound(KeyError): pass
class Cancelled(Exception): pass

@dataclass
class Job:
    id: int
    name: str
    payload: Any
    state: str
    priority: int
    attempts: int
    max_attempts: int
    timeout: Optional[float]
    run_at: float
    lease_expires: Optional[float]
    worker_id: Optional[str]
    cancel_requested: bool
    last_error: Optional[str]
    result: Any
    key: Optional[str]
    created: float
    finished: Optional[float]

COLS = "id, name, payload, state, priority, attempts, max_attempts, timeout, run_at, lease_expires, worker_id, cancel_requested, last_error, result, key, created, finished"

def _row(r):
    return Job(r[0], r[1], json.loads(r[2]), r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], bool(r[11]), r[12], None if r[13] is None else json.loads(r[13]), r[14], r[15], r[16])

def _int(v, name, lo):
    if isinstance(v, bool) or not isinstance(v, int) or v < lo: raise ValueError(f"{name} must be an integer of at least {lo}")
    return v

def _locked(fn):
    @functools.wraps(fn)
    def w(self, *a, **k):
        with self._lock: return fn(self, *a, **k)
    return w

class JobQueue:
    def __init__(self, path=":memory:", clock=time.time, lease_seconds=30.0, retry_base=1.0, retry_cap=60.0):
        self._lock = threading.RLock(); self.clock, self.lease_seconds, self.retry_base, self.retry_cap = clock, lease_seconds, retry_base, retry_cap
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, priority INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, "
                        "max_attempts INTEGER NOT NULL, timeout REAL, run_at REAL NOT NULL, lease_expires REAL, worker_id TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, last_error TEXT, result TEXT, key TEXT UNIQUE, created REAL NOT NULL, finished REAL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS jobs_pick ON jobs(state, priority, run_at, id)")

    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    def _tx(self, fn):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try: out = fn(); self.db.execute("COMMIT"); return out
            except BaseException: self.db.execute("ROLLBACK"); raise

    @_locked
    def get(self, job_id):
        r = self.db.execute(f"SELECT {COLS} FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if r is None: raise JobNotFound(job_id)
        return _row(r)

    @_locked
    def counts(self):
        out = {s: 0 for s in STATES}
        for s, n in self.db.execute("SELECT state, COUNT(*) FROM jobs GROUP BY state"): out[s] = n
        return out

    def enqueue(self, name, payload=None, *, priority=0, max_attempts=3, timeout=None, delay=0.0, key=None):
        if not isinstance(name, str) or not name.strip(): raise ValueError("name must be a non-empty string")
        _int(priority, "priority", -10**9); _int(max_attempts, "max_attempts", 1)
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0): raise ValueError("timeout must be a positive number")
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay < 0: raise ValueError("delay must be a non-negative number")
        if key is not None and (not isinstance(key, str) or not key): raise ValueError("key must be a non-empty string")
        try: data = json.dumps(payload)
        except (TypeError, ValueError): raise ValueError("payload must be JSON-serializable")
        def go():
            if key is not None:
                r = self.db.execute("SELECT id FROM jobs WHERE key = ?", (key,)).fetchone()
                if r: return r[0]
            now = self.clock()
            return self.db.execute("INSERT INTO jobs(name, payload, state, priority, max_attempts, timeout, run_at, key, created) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)",
                                   (name.strip(), data, priority, max_attempts, timeout, now + delay, key, now)).lastrowid
        return self._tx(go)

    def _recover(self):
        now, n = self.clock(), 0
        for jid, att, mx in self.db.execute("SELECT id, attempts, max_attempts FROM jobs WHERE state = 'running' AND lease_expires < ?", (now,)).fetchall():
            if att >= mx: self.db.execute("UPDATE jobs SET state = 'failed', last_error = 'worker lost (lease expired)', lease_expires = NULL, worker_id = NULL, finished = ? WHERE id = ?", (now, jid))
            else: self.db.execute("UPDATE jobs SET state = 'queued', last_error = 'worker lost (lease expired)', lease_expires = NULL, worker_id = NULL, run_at = ? WHERE id = ?", (now, jid))
            n += 1
        return n

    def recover(self): return self._tx(self._recover)

    def claim(self, worker_id):
        def go():
            self._recover(); now = self.clock()
            r = self.db.execute(f"SELECT {COLS} FROM jobs WHERE state = 'queued' AND run_at <= ? ORDER BY priority DESC, run_at ASC, id ASC LIMIT 1", (now,)).fetchone()
            if r is None: return None
            self.db.execute("UPDATE jobs SET state = 'running', attempts = attempts + 1, lease_expires = ?, worker_id = ? WHERE id = ?", (now + self.lease_seconds, worker_id, r[0]))
            return self.get(r[0])
        return self._tx(go)

    def _mine(self, job):
        r = self.db.execute("SELECT state, attempts, worker_id, cancel_requested FROM jobs WHERE id = ?", (job.id,)).fetchone()
        return r is not None and r[0] == "running" and r[1] == job.attempts and r[2] == job.worker_id, (r[3] if r else 0)

    def complete(self, job, result=None):
        try: data = json.dumps(result)
        except (TypeError, ValueError): return self.fail(job, "TypeError: result is not JSON-serializable", retry=True) in ("queued", "failed", "cancelled")
        def go():
            ok, cancel = self._mine(job)
            if not ok: return False
            now = self.clock()
            if cancel: self.db.execute("UPDATE jobs SET state = 'cancelled', lease_expires = NULL, finished = ? WHERE id = ?", (now, job.id))
            else: self.db.execute("UPDATE jobs SET state = 'succeeded', result = ?, last_error = NULL, lease_expires = NULL, finished = ? WHERE id = ?", (data, now, job.id))
            return True
        return self._tx(go)

    def fail(self, job, error, retry=True):
        def go():
            ok, cancel = self._mine(job)
            if not ok: return None
            now = self.clock()
            if cancel: self.db.execute("UPDATE jobs SET state = 'cancelled', last_error = ?, lease_expires = NULL, finished = ? WHERE id = ?", (error, now, job.id)); return "cancelled"
            if retry and job.attempts < job.max_attempts:
                delay = min(self.retry_cap, self.retry_base * 2 ** (job.attempts - 1))
                self.db.execute("UPDATE jobs SET state = 'queued', last_error = ?, lease_expires = NULL, worker_id = NULL, run_at = ? WHERE id = ?", (error, now + delay, job.id)); return "queued"
            self.db.execute("UPDATE jobs SET state = 'failed', last_error = ?, lease_expires = NULL, finished = ? WHERE id = ?", (error, now, job.id)); return "failed"
        return self._tx(go)

    def heartbeat(self, job):
        def go():
            ok, _ = self._mine(job)
            if ok: self.db.execute("UPDATE jobs SET lease_expires = ? WHERE id = ?", (self.clock() + self.lease_seconds, job.id))
            return ok
        return self._tx(go)

    def cancel(self, job_id):
        def go():
            r = self.db.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if r is None: raise JobNotFound(job_id)
            if r[0] == "queued": self.db.execute("UPDATE jobs SET state = 'cancelled', finished = ? WHERE id = ?", (self.clock(), job_id)); return "cancelled"
            if r[0] == "running": self.db.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job_id,)); return "requested"
            return False
        return self._tx(go)

    def cancel_requested(self, job_id):
        with self._lock:
            r = self.db.execute("SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return bool(r and r[0])

class JobContext:
    def __init__(self, queue, job): self._q, self._job = queue, job; self.job_id, self.attempt = job.id, job.attempts
    @property
    def cancelled(self): return self._q.cancel_requested(self.job_id)
    def check(self):
        if self.cancelled: raise Cancelled()
    def heartbeat(self): return self._q.heartbeat(self._job)

class Worker:
    def __init__(self, queue, handlers, worker_id=None, poll_interval=0.05):
        self.queue, self.handlers, self.poll = queue, dict(handlers), poll_interval
        self.worker_id = worker_id or f"w-{uuid.uuid4().hex[:8]}"; self._stop = threading.Event(); self._threads = []

    def run_once(self):
        job = self.queue.claim(self.worker_id)
        if job is None: return False
        fn = self.handlers.get(job.name)
        if fn is None: self.queue.fail(job, f"LookupError: no handler for {job.name}", retry=False); return True
        box = {}
        def target():
            try: box["result"] = fn(job.payload, JobContext(self.queue, job))
            except BaseException as e: box["error"] = e
        t = threading.Thread(target=target, daemon=True); t.start(); started = time.monotonic(); slice_ = max(0.02, min(1.0, self.queue.lease_seconds / 3))
        while t.is_alive():
            left = None if job.timeout is None else job.timeout - (time.monotonic() - started)
            if left is not None and left <= 0:
                self.queue.fail(job, f"TimeoutError: job exceeded {job.timeout} seconds"); return True
            t.join(slice_ if left is None else min(slice_, left))
            if t.is_alive(): self.queue.heartbeat(job)
        if "error" in box:
            e = box["error"]; self.queue.fail(job, f"{type(e).__name__}: {e}")
        else: self.queue.complete(job, box.get("result"))
        return True

    def run_until_empty(self, max_jobs=None):
        n = 0
        while (max_jobs is None or n < max_jobs) and self.run_once(): n += 1
        return n

    def start(self, threads=1):
        self._stop.clear()
        def loop():
            while not self._stop.is_set():
                if not self.run_once(): self._stop.wait(self.poll)
        for _ in range(threads): t = threading.Thread(target=loop, daemon=True); t.start(); self._threads.append(t)
        return self

    def stop(self, wait=True):
        self._stop.set()
        if wait:
            for t in self._threads: t.join()
        self._threads = []
