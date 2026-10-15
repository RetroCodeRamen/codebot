"""Adaptive task sizing: split tasks that keep failing, remember what size works on this machine, tell the planner."""
import fcntl, json, os, re, shlex, subprocess, sys
from . import planops, tasks as T
from .proc import run_group
from .config import now, append_jsonl

# ---------- splitting ----------
def _conjuncts(cmd):
    """Split a shell command on top-level ` && ` (quote-aware). Returns the parts."""
    parts, cur, q, i = [], "", None, 0
    while i < len(cmd):
        c = cmd[i]
        if q:
            cur += c
            if c == q and cmd[i - 1] != "\\": q = None
        elif c in "\"'": q = c; cur += c
        elif cmd.startswith("&&", i): parts.append(cur.strip()); cur = ""; i += 1
        else: cur += c
        i += 1
    if cur.strip(): parts.append(cur.strip())
    return [p for p in parts if p]

def _file_check(f):
    q = shlex.quote(f)
    if f.endswith(".py"): return f"test -s {q} && python3 -m py_compile {q}"
    if f.endswith(".sh"): return f"test -s {q} && bash -n {q}"
    if f.endswith(".json"): return f"test -s {q} && python3 -c \"import json,sys;json.load(open(sys.argv[1]))\" {q}"
    return f"test -s {q}"

def mechanical_split(task):
    """No model needed. Prefer splitting by listed files; else by the conjuncts of the task's own check."""
    files = [f for f in task.get("files") or [] if f]
    if len(files) >= 2:
        n = min(len(files), 6)
        return {"subtasks": [{"title": f"{task['title']} (part {i}/{n}: {f})", "check": _file_check(f), "files": [f], "size": "S"}
                             for i, f in enumerate(files[:n], 1)]}
    parts = _conjuncts(task.get("check") or "")
    if 2 <= len(parts) <= 6:
        return {"subtasks": [{"title": f"{task['title']} (part {i}/{len(parts)}: {p[:60]})", "check": p, "size": "S"}
                             for i, p in enumerate(parts, 1)]}
    return None

def propose(proj, task, last_error="", timeout=1800):
    """SPLIT_CMD (a hook: reads CODEBOT_TASK_FILE, prints JSON {"subtasks":[...],"fixtures":{...}}); with WORKER=native the
    model-driven splitter is the default hook and the mechanical splitter is the fallback."""
    cmd = proj.get("SPLIT_CMD")
    if not cmd and proj.get("WORKER") == "native" and proj.get("SPLITTER", "auto") != "mechanical":
        cmd = f"{sys.executable} -m codebot.splitter"
    if cmd:
        tfile = proj.p(".split_task.json")
        with open(tfile, "w") as f: json.dump({"task": task, "error": last_error}, f)
        try:
            rc, out, _, timed_out = run_group(cmd, timeout, cwd=proj.rundir,
                               env=dict(os.environ, CODEBOT_TASK_FILE=tfile, CODEBOT_TASK_ID=task["id"], CODEBOT_STATE=proj.state, CODEBOT_RUNDIR=proj.rundir,
                                        CODEBOT_HOME=proj.home, CODEBOT_PROJECT=proj.name,
                                        PYTHONPATH=os.path.dirname(os.path.dirname(os.path.realpath(__file__))) + os.pathsep + os.environ.get("PYTHONPATH", "")))
            prop = None if timed_out else json.loads(out)
            if isinstance(prop, dict) and prop.get("subtasks"): return prop
        except (ValueError, OSError): pass
        if proj.get("SPLIT_CMD"): return None             # an explicit hook that fails means "no split", not "guess"
    return mechanical_split(task)

def auto_split(proj, tid, last_error="", actor="harness", timeout=1800):
    """Split one task into subtasks with their own frozen checks. Returns {"ok":..., "children":[...]} (ok False = no split)."""
    store = T.TaskStore(proj); data = store.load(); task = T.get(data, tid)
    if task is None: return {"ok": False, "error": f"unknown task {tid}"}
    if task.get("split_into"): return {"ok": False, "error": "already split"}
    prop = propose(proj, task, last_error, timeout)
    if not prop: return {"ok": False, "error": "no split available"}
    subs = [s for s in prop["subtasks"] if (s.get("check") or "").strip() != (task.get("check") or "").strip()]
    if len(subs) < 2: return {"ok": False, "error": "split would not simplify the task"}
    r = planops.apply(proj, {"op": "split_task", "task": tid, "subtasks": subs, "fixtures": prop.get("fixtures"),
                              "reason": f"auto-split after repeated failure of {tid}"}, actor)
    if r["ok"]: record(proj, task, "split")
    return {"ok": r["ok"], "children": r.get("result"), "error": r.get("error")}

# ---------- stats ----------
_TYPES = [("test", r"\b(tests?|spec|coverage)\b"), ("fix", r"\b(fix|bug|error|repair)\b"), ("refactor", r"\b(refactor|rename|cleanup|clean up|restructure)\b"),
          ("docs", r"\b(docs?|readme|document)\b"), ("config", r"\b(config|setup|install|build|ci)\b"), ("analysis", r"\b(find|analy[sz]e|inspect|investigate|reverse)\b")]

def kind_of(title):
    t = (title or "").lower()
    for k, rx in _TYPES:
        if re.search(rx, t): return k
    return "implement"

def _bucket(): return {"tasks": 0, "done": 0, "first_try": 0, "attempts": 0, "split": 0, "blocked": 0}

def _update(path, key_size, key_type, outcome, attempts):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            with open(path) as f: s = json.load(f)
        except (OSError, ValueError): s = {"by_size": {}, "by_type": {}}
        for group, key in (("by_size", key_size), ("by_type", key_type)):
            b = s[group].setdefault(key, _bucket())
            b["tasks"] += 1; b["attempts"] += attempts
            if outcome == "done": b["done"] += 1; b["first_try"] += 1 if attempts <= 1 else 0
            elif outcome in ("split", "blocked"): b[outcome] += 1
        with open(path, "w") as f: json.dump(s, f, indent=1)
        fcntl.flock(lf, fcntl.LOCK_UN)

def record(proj, task, outcome):
    """Terminal outcome of a task: done | split | blocked. Updates project stats and the machine-wide stats."""
    size, kind, att = (task.get("size") or "M").upper(), kind_of(task["title"]), max(1, task.get("attempts", 1))
    _update(proj.p("stats.json"), size, kind, outcome, att)
    _update(os.path.join(proj.memdir, "sizing.json"), size, kind, outcome, att)

def load_stats(path):
    try:
        with open(path) as f: return json.load(f)
    except (OSError, ValueError): return {"by_size": {}, "by_type": {}}

def advice_line(proj, min_samples=5, target=0.7):
    """One line for the planner prompt: the largest size whose first-try success meets the target on this machine."""
    mine, glob = load_stats(proj.p("stats.json")), load_stats(os.path.join(proj.memdir, "sizing.json"))
    src = mine if sum(b["tasks"] for b in mine["by_size"].values()) >= min_samples else glob
    sizes = src["by_size"]
    if sum(b["tasks"] for b in sizes.values()) < min_samples:
        return "(no history yet: cut tasks small, one file or one behaviour each, size S)"
    desc = []
    for k in ("S", "M", "L"):
        b = sizes.get(k)
        if b and b["tasks"]:
            desc.append(f"{k}: {round(100 * b['first_try'] / b['tasks'])}% first-try, {round(100 * b['split'] / b['tasks'])}% needed splitting (n={b['tasks']})")
    best = "S"
    for k in ("S", "M", "L"):
        b = sizes.get(k)
        if b and b["tasks"] >= min_samples and b["first_try"] / b["tasks"] >= target: best = k
    return f"{'; '.join(desc)}. Cut tasks to size {best} or smaller."
