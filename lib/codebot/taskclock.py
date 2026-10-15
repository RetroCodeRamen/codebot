"""Per-task wall-clock budgets (TASK_MAX_MINUTES; 0 = off, the default for ordinary projects; benchmark projects set 90).

One budget belongs to one ROOT task: `T3`, `T3.1`, `T3.1.2` and anything the worker adds while running T3 all draw from the budget of `T3`, so retrying,
splitting or replanning can never reset it. Independent tasks of the original plan each have their own budget; there is no project-wide budget.

Persistence (`taskclock.json` in the harness state dir, written atomically under a file lock; the worker cannot reach it):
  used_s      seconds charged by closed intervals (monotonic time measured inside the engine process)
  open        {"since", "hb", "pid"} while a step is running; a heartbeat thread refreshes `hb` every few seconds. If the process dies, the
              next `settle()` charges hb - since, so a restart can never refund time (it can lose at most one heartbeat interval in the other direction).
Thermal waiting is part of the budget (it is wall time); it is measured separately from thermal.jsonl events tagged with the task id.
Suspend/hibernate: CLOCK_MONOTONIC does not advance while the machine sleeps and a dead process is charged only up to its last heartbeat, so sleeping time is not charged."""
import fcntl, json, os, tempfile, threading, time
from contextlib import contextmanager

FILE = "taskclock.json"
HEARTBEAT_S = 2.0

def root_of(task):
    """Budget owner of a task (dict or id): the explicit `budget_root` if the harness set one, else the first dotted component."""
    if isinstance(task, dict): return task.get("budget_root") or str(task["id"]).split(".")[0]
    return str(task).split(".")[0]

def limit_s(proj):
    """Per-task budget in seconds (0 = unlimited). Minutes may be fractional so tests can use very short budgets."""
    v = proj.get("TASK_MAX_MINUTES", 0, float)
    return max(0.0, v * 60.0)

def _path(proj): return proj.p(FILE)

@contextmanager
def _locked(proj):
    with open(_path(proj) + ".lock", "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(lf, fcntl.LOCK_UN)

def load(proj):
    try:
        with open(_path(proj)) as f: d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("roots"), dict): return d
    except (OSError, ValueError): pass
    return {"version": 1, "roots": {}}

def _save(proj, d):
    fd, tmp = tempfile.mkstemp(dir=proj.state, prefix=".taskclock.")
    with os.fdopen(fd, "w") as f: json.dump(d, f, indent=1)
    os.replace(tmp, _path(proj))

def _root(d, root):
    return d["roots"].setdefault(root, {"used_s": 0.0, "steps": 0, "first_start": None, "last_end": None, "open": None, "state": "active", "remaining_s": None, "charged_after_crash_s": 0.0})

def settle(proj, wall=time.time):
    """Charge every interval left open by a step that never finished (crash, kill, reboot). Safe to call at any time a step is not running."""
    with _locked(proj):
        d = load(proj); n = 0
        for name, r in d["roots"].items():
            o = r.get("open")
            if o:
                lost = max(0.0, float(o.get("hb", o["since"])) - float(o["since"]))
                r["used_s"] = round(r["used_s"] + lost, 3); r["charged_after_crash_s"] = round(r.get("charged_after_crash_s", 0.0) + lost, 3)
                r["open"] = None; r["last_end"] = o.get("hb"); n += 1
        if n: _save(proj, d)
        return n

def used(proj, root):
    return float(load(proj)["roots"].get(root, {}).get("used_s", 0.0))

def remaining(proj, root):
    lim = limit_s(proj)
    return None if not lim else lim - used(proj, root)

def mark(proj, root, state, remaining_s=None):
    with _locked(proj):
        d = load(proj); r = _root(d, root); r["state"] = state
        if remaining_s is not None: r["remaining_s"] = round(remaining_s, 3)
        _save(proj, d)

class Handle:
    """An open interval for one root task. `remaining()` is exact (monotonic); the heartbeat only protects against a dead process."""
    def __init__(self, proj, root, mono=time.monotonic, wall=time.time, heartbeat=HEARTBEAT_S):
        self.proj, self.root, self.mono, self.wall, self.hb_s = proj, root, mono, wall, heartbeat
        self.lim = limit_s(proj); self.t0 = mono(); self.closed = False
        with _locked(proj):
            d = load(proj); r = _root(d, root); self.base = float(r["used_s"]); r["steps"] += 1
            r["first_start"] = r["first_start"] or wall(); r["state"] = "active"
            r["open"] = {"since": wall(), "hb": wall(), "pid": os.getpid()}; _save(proj, d)
        self._stop = threading.Event(); self._th = None
        if heartbeat:
            self._th = threading.Thread(target=self._beat, daemon=True); self._th.start()
    def _beat(self):
        while not self._stop.wait(self.hb_s):
            try:
                with _locked(self.proj):
                    d = load(self.proj); r = d["roots"].get(self.root)
                    if r and r.get("open"): r["open"]["hb"] = self.wall(); _save(self.proj, d)
            except OSError: pass
    def elapsed(self): return self.mono() - self.t0
    def used(self): return self.base + self.elapsed()
    def remaining(self):
        """Seconds left in this root's budget, or None when budgets are off."""
        return None if not self.lim else self.lim - self.used()
    def expired(self):
        r = self.remaining(); return r is not None and r <= 0
    def cap(self, seconds):
        """A timeout for a sub-operation: never longer than what is left (at least 1 s so an expired budget still gets a clean, immediate failure)."""
        r = self.remaining()
        return seconds if r is None else max(1, min(seconds, int(r) + 1))
    def end(self, state=None):
        if self.closed: return
        self.closed = True; self._stop.set()
        if self._th: self._th.join(timeout=2)
        el = self.elapsed()
        with _locked(self.proj):
            d = load(self.proj); r = _root(d, self.root)
            r["used_s"] = round(self.base + el, 3); r["open"] = None; r["last_end"] = self.wall()
            if state: r["state"] = state
            r["remaining_s"] = None if not self.lim else round(self.lim - r["used_s"], 3)
            _save(self.proj, d)

def begin(proj, task, **kw):
    """Start charging the root of `task`. Returns a Handle, or None when budgets are off (TASK_MAX_MINUTES unset or 0)."""
    if not limit_s(proj): return None
    return Handle(proj, root_of(task), **kw)

def thermal_wait_s(proj, root):
    """Seconds this root task spent paused by the thermal guard (already included in its wall time; reported separately)."""
    total = 0.0
    try:
        with open(proj.p("thermal.jsonl")) as f:
            for ln in f:
                try: e = json.loads(ln)
                except ValueError: continue
                if e.get("event") == "resume" and e.get("task") and root_of(e["task"]) == root: total += float(e.get("waited") or 0)
    except OSError: pass
    return total

def summary(proj):
    """{root: {used_s, thermal_s, steps, state, remaining_s, limit_s, charged_after_crash_s}} for reports."""
    d, lim, out = load(proj), limit_s(proj), {}
    for root, r in d["roots"].items():
        out[root] = {"used_s": round(r["used_s"], 1), "thermal_s": round(thermal_wait_s(proj, root), 1), "steps": r["steps"], "state": r["state"],
                     "remaining_s": r.get("remaining_s"), "limit_s": lim or None, "charged_after_crash_s": r.get("charged_after_crash_s", 0.0),
                     "first_start": r.get("first_start"), "last_end": r.get("last_end")}
    return out
