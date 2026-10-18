"""Budgets and stuck detection. Signals never loop: they hand the task to the escalation ladder."""
import json, os, time, hashlib
from .config import append_jsonl, now

DEFAULTS = {"TASK_MAX_ATTEMPTS": 8, "STUCK_SAME_FAILURE": 3, "STUCK_NO_PROGRESS": 6, "PINGPONG_WINDOW": 6,
            "MAX_ITERATIONS": 300, "PROJECT_MAX_MINUTES": 0}   # 0 = no wall-clock cap

def limits(proj): return {k: proj.get(k, d, int) for k, d in DEFAULTS.items()}

def load_attempts(proj):
    out = []
    try:
        with open(proj.p("attempts.jsonl")) as f:
            for ln in f:
                try: r = json.loads(ln)
                except ValueError: continue
                if "attempt" in r:
                    r.setdefault("passed", False); r.setdefault("sig", "")     # an attempt cut by the task budget has no verdict: it did not pass
                    out.append(r)
    except OSError: pass
    return out

def started_epoch(proj):
    for rel in ("started.at",):
        try:
            with open(proj.p(rel)) as f: return int(f.read().strip())
        except (OSError, ValueError): pass
    return int(time.time())

def project_budget(proj):
    """None, or (kind, message): a project-level cap was hit. The supervisor stops the run and reports."""
    L = limits(proj)
    n = len(load_attempts(proj))
    if L["MAX_ITERATIONS"] and n >= L["MAX_ITERATIONS"]:
        return ("iterations", f"reached the cap of {L['MAX_ITERATIONS']} attempts")
    if L["PROJECT_MAX_MINUTES"]:
        mins = (time.time() - started_epoch(proj)) / 60
        if mins >= L["PROJECT_MAX_MINUTES"]: return ("wallclock", f"ran {int(mins)} min (cap {L['PROJECT_MAX_MINUTES']})")
    return None

def _flipped(seq):
    """True if a file went A ... B ... A with B != A (edited back and forth)."""
    seen_change = False; first = None; prev = None
    hist = []
    for h in seq:
        if prev is not None and h != prev: seen_change = True
        if seen_change and h in hist[:-1] and h != prev: return True
        hist.append(h); prev = h
    return False

def signals(proj, task):
    """List of (name, message) for the task that just failed."""
    L, allatts = limits(proj), load_attempts(proj)
    base = task.get("attempts_base", 0)          # attempts before the last human answer do not count
    mine = [a for a in allatts if a["task"] == task["id"]][base:]
    used = task["attempts"] - base
    out = []
    n = L["STUCK_SAME_FAILURE"]
    if n and len(mine) >= n:
        tail = mine[-n:]
        if all(not a["passed"] for a in tail) and len({a["sig"] for a in tail}) == 1 and tail[-1]["sig"]:
            out.append(("same_failure", f"the same check failure {n} times in a row"))
    if L["TASK_MAX_ATTEMPTS"] and used >= L["TASK_MAX_ATTEMPTS"]:
        out.append(("task_budget", f"{used} attempts used (cap {L['TASK_MAX_ATTEMPTS']})"))
    nop = 0
    for a in reversed(allatts):
        if a["passed"]: break
        nop += 1
    if L["STUCK_NO_PROGRESS"] and nop >= L["STUCK_NO_PROGRESS"]:
        out.append(("no_progress", f"{nop} attempts in a row without any task completing"))
    win = mine[-L["PINGPONG_WINDOW"]:] if L["PINGPONG_WINDOW"] else []
    files = {f for a in win for f in (a.get("files") or {})}
    for f in sorted(files):
        seq = [a["files"][f] for a in win if f in (a.get("files") or {})]
        if len(seq) >= 3 and _flipped(seq): out.append(("edit_ping_pong", f"{f} was edited back to an earlier version")); break
    return out
