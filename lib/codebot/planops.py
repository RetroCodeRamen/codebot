"""Controlled replanning. The model may ADD, SPLIT or REORDER tasks. It may never delete a task, edit or weaken a
check, rename, or set a status. Every op (accepted or rejected) is logged with a reason in plan.log."""
import os, re
from . import tasks as T, checks as C
from .config import now, append_jsonl

ALLOWED = ("add_task", "split_task", "reorder")
SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,60}$")

class Rejected(Exception):
    pass

def _summary(data): return [(t["id"], t["status"]) for t in data["tasks"]]

def _fixtures(proj, fx):
    for name, content in (fx or {}).items():
        if not SAFE_NAME.match(name) or name.startswith("."): raise Rejected(f"unsafe fixture name {name!r}")
        if os.path.exists(os.path.join(C.checks_dir(proj), name)): raise Rejected(f"fixture {name} already exists (locked)")
    for name, content in (fx or {}).items():
        os.makedirs(C.checks_dir(proj), exist_ok=True)
        with open(os.path.join(C.checks_dir(proj), name), "w") as f: f.write(content)

def _need_check(proj, spec):
    if not (spec.get("check") or "").strip() and not proj.get("ALLOW_UNCHECKED"):
        raise Rejected("every new task needs a check")

def _next_num(data):
    nums = [int(m.group(1)) for t in data["tasks"] if (m := re.fullmatch(r"T(\d+)", t["id"]))]
    return (max(nums) if nums else 0) + 1

def _add(proj, data, op):
    spec = op; _need_check(proj, spec)
    if not (spec.get("title") or "").strip(): raise Rejected("title required")
    deps = list(spec.get("deps") or [])
    for d in deps:
        if T.get(data, d) is None: raise Rejected(f"unknown dep {d}")
    t = T.new_task(f"T{_next_num(data)}", spec["title"].strip(), (spec.get("check") or None), spec.get("files"), spec.get("size"), deps)
    pos = len(data["tasks"])
    if op.get("after"):
        a = T.get(data, op["after"])
        if a is None: raise Rejected(f"unknown task {op['after']} in 'after'")
        pos = data["tasks"].index(a) + 1
    _fixtures(proj, op.get("fixtures")); data["tasks"].insert(pos, t); return [t["id"]]

def _split(proj, data, op):
    parent = T.get(data, op.get("task", ""))
    if parent is None: raise Rejected(f"unknown task {op.get('task')!r}")
    if parent["status"] not in ("pending", "blocked"): raise Rejected(f"cannot split a {parent['status']} task")
    subs = op.get("subtasks") or []
    if not (2 <= len(subs) <= 6): raise Rejected("split needs 2-6 subtasks")
    for s in subs: _need_check(proj, s)
    _fixtures(proj, op.get("fixtures"))
    kids, prev_dep = [], list(parent["deps"])
    for i, s in enumerate(subs, 1):
        k = T.new_task(f"{parent['id']}.{i}", s["title"].strip(), s.get("check") or None, s.get("files"), s.get("size") or "S", list(prev_dep), parent["id"])
        kids.append(k); prev_dep = [k["id"]]                 # children run in order
    idx = data["tasks"].index(parent); data["tasks"][idx:idx] = kids
    parent["deps"] = list(parent["deps"]) + [k["id"] for k in kids]   # parent keeps its ORIGINAL check as the final gate
    parent["split_into"] = [k["id"] for k in kids]
    if parent["status"] == "blocked": parent["status"] = "pending"; parent["blocked_question"] = None
    parent["ladder"] = 1; parent["rung_tries"] = 0
    return [k["id"] for k in kids]

def _reorder(proj, data, op):
    order = op.get("order") or []
    movable = [t for t in data["tasks"] if t["status"] in ("pending", "blocked")]
    if sorted(order) != sorted(t["id"] for t in movable): raise Rejected("order must list exactly the pending/blocked tasks")
    by = {t["id"]: t for t in movable}
    new_pending = iter([by[i] for i in order])
    rebuilt = [next(new_pending) if t["status"] in ("pending", "blocked") else t for t in data["tasks"]]
    pos = {t["id"]: i for i, t in enumerate(rebuilt)}
    for t in rebuilt:
        for d in t["deps"]:
            if pos[d] > pos[t["id"]]: raise Rejected(f"{t['id']} would run before its dependency {d}")
    data["tasks"][:] = rebuilt; return order

_DISPATCH = {"add_task": _add, "split_task": _split, "reorder": _reorder}

def apply(proj, op, actor="model"):
    """Apply one op. Returns {"ok": bool, ...}. Always logs. Never raises on a bad op."""
    store = T.TaskStore(proj); name = str(op.get("op", ""))
    with store.locked():
        store.check_integrity(); data = store.load(); before = _summary(data)
        try:
            if name not in ALLOWED: raise Rejected(f"op {name!r} is not allowed (allowed: {', '.join(ALLOWED)})")
            if not (op.get("reason") or "").strip(): raise Rejected("a reason is required for every plan change")
            result = _DISPATCH[name](proj, data, op)
            C.freeze(proj, data, only_new=True)           # new checks are frozen immediately, old ones untouched
            store.save(data)
            out = {"ok": True, "op": name, "result": result}
        except Rejected as e:
            out = {"ok": False, "op": name, "error": str(e)}
        append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": actor, "op": name, "reason": (op.get("reason") or ""),
                     "ok": out["ok"], "error": out.get("error"), "before": before,
                     "after": _summary(store.load()) if out["ok"] else before})
    return out
