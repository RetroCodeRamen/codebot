"""The engine: one `step` = one attempt at the next task, decided and judged by the harness, not the model."""
import json, os, subprocess, time
from . import tasks as T, checks as C, gitops as G, budgets as B
from .config import Project, now, append_jsonl
from .planparse import parse_plan

def freeze_project(proj, allow_unchecked=False):
    """PLAN.md -> tasks.json (+ frozen checks). Additive: PLAN.md and other state files are kept."""
    store = T.TaskStore(proj)
    if store.exists(): raise SystemExit(f"{proj.name}: already frozen (tasks.json exists)")
    plan = proj.p("PLAN.md")
    if not os.path.exists(plan): raise SystemExit(f"{proj.name}: no PLAN.md; run: codebot plan {proj.name}")
    with open(plan) as f: head, tasks = parse_plan(f.read())
    if not tasks: raise SystemExit(f"{proj.name}: no tasks found under '# Tasks' in PLAN.md")
    missing = [t["id"] for t in tasks if not t.get("check") and t["status"] != "done"]
    if missing and not allow_unchecked:
        raise SystemExit(f"{proj.name}: tasks without a check: {', '.join(missing)}. "
                         f"Add a `  - check: `<command>`` line to each (or set ALLOW_UNCHECKED=1).")
    data = {"version": 1, "project": proj.name, "created": now(), "plan_head": head, "tasks": tasks}
    os.makedirs(C.checks_dir(proj), exist_ok=True)
    with store.locked():
        C.freeze(proj, data); store.save(data)
    append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "harness", "op": "freeze", "reason": "initial freeze",
                                       "tasks": len(tasks)})
    return data

def _worker_cmd(proj):
    return proj.get("WORKER_CMD")

def run_worker(proj, task, attempt, last_error):
    """Pluggable worker. Contract: runs in the code dir, gets the task via env/file, may edit code; its exit
    status and any claim of success are ignored: only the frozen check decides."""
    cmd = _worker_cmd(proj)
    if not cmd: return {"rc": None, "note": "no WORKER_CMD configured"}
    tfile = proj.p(".current_task.json"); C.jdump(tfile, task, indent=1)
    efile = proj.p(".last_error.txt")
    with open(efile, "w") as f: f.write(last_error or "")
    env = dict(os.environ, CODEBOT_TASK_FILE=tfile, CODEBOT_TASK_ID=task["id"], CODEBOT_ATTEMPT=str(attempt),
               CODEBOT_LAST_ERROR_FILE=efile, CODEBOT_RUNDIR=proj.rundir, CODEBOT_STATE=proj.state,
               CODEBOT_CHECKS=C.checks_dir(proj))
    t0 = time.time()
    try:
        r = subprocess.run(cmd, shell=True, cwd=proj.rundir, env=env, capture_output=True, text=True,
                           timeout=proj.get("ITER_TIMEOUT", 2700, int))
        return {"rc": r.returncode, "secs": round(time.time() - t0, 1), "tail": (r.stdout + r.stderr)[-800:]}
    except subprocess.TimeoutExpired:
        return {"rc": 124, "secs": round(time.time() - t0, 1), "tail": "[worker timed out]"}

def err_text(task, res):
    """What the worker is told about a failing check. Silent checks (grep -q, test -f) get a description."""
    out = (res.get("out") or "").strip()
    if out: return out[-1500:]
    return f"The acceptance check `{task.get('check')}` exited with status {res['rc']} and printed nothing. It is run in the code directory."

def step(proj, fresh=False):
    """One attempt. Returns an event dict for the supervisor (bash loop) to act on.
    fresh=True: reset the code dir to the last verified checkpoint before the worker runs."""
    store = T.TaskStore(proj)
    if not store.exists(): return {"event": "error", "error": "not frozen: run `codebot freeze`"}
    over = B.project_budget(proj)
    if over: return {"event": "budget", "kind": over[0], "message": over[1]}
    with store.locked():
        tamper = [] if store.check_integrity() else ["tasks.json:edited-outside-engine"]
        data = store.load()
        tamper += C.verify(proj, data) if C.is_frozen(proj) else []
        if tamper:
            C.restore(proj, data, tamper)
            append_jsonl(proj.p("attempts.jsonl"), {"ts": now(), "event": "tamper", "findings": tamper})
        task = T.next_task(data)
        if task is None:
            c = T.counts(data)
            if c["pending"] == 0 and c["running"] == 0:
                return {"event": "all_done" if c["blocked"] == 0 else "done_with_blocked", "counts": c, "tamper": tamper}
            return {"event": "stalled", "counts": c, "tamper": tamper, "note": "remaining tasks wait on blocked ones"}
        if not task.get("check"):
            if not proj.get("ALLOW_UNCHECKED", None, str):
                return {"event": "error", "task": task["id"], "error": "task has no check"}
        G.ensure_baseline(proj)
        if fresh: G.reset_to_last_good(proj)
        # Run the check BEFORE the worker: if it already passes there is nothing to do (e.g. the final gate of a split
        # task, or a weak check), and if it fails its output is the freshest, most accurate error for the worker.
        pre = C.run_check(proj, task, proj.get("CHECK_TIMEOUT", 600, int)) if task.get("check") else None
        if pre is not None and pre["rc"] == 0:
            task["status"] = "done"; task["commit"] = G.checkpoint(proj, task)
            task["history"].append({"ts": now(), "precheck_pass": True, "gate": bool(task.get("split_into"))})
            store.save(data)
            append_jsonl(proj.p("attempts.jsonl"), {"ts": now(), "task": task["id"], "event": "precheck_pass",
                                                     "gate": bool(task.get("split_into"))})
            if not task.get("split_into"):
                append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "harness", "op": "weak_check", "ok": True,
                             "reason": f"{task['id']} check passed before any work"})
            return {"event": "task_done", "task": task["id"], "attempt": task["attempts"], "precheck": True, "tamper": tamper}
        task["status"] = "running"; task["attempts"] += 1; attempt = task["attempts"]
        store.save(data)
        last = err_text(task, pre) if pre else ""
    w = run_worker(proj, task, attempt, last)
    with store.locked():
        tamper2 = [] if store.check_integrity() else ["tasks.json:edited-outside-engine"]
        data = store.load(); task = T.get(data, task["id"])
        tamper2 += C.verify(proj, data) if C.is_frozen(proj) else []
        if tamper2:
            C.restore(proj, data, tamper2)
            append_jsonl(proj.p("attempts.jsonl"), {"ts": now(), "event": "tamper", "task": task["id"], "findings": tamper2})
        res = C.run_check(proj, task, proj.get("CHECK_TIMEOUT", 600, int)) if task.get("check") else {"rc": 0, "out": "", "sig": "", "secs": 0}
        passed = res["rc"] == 0 and not tamper2
        rec = {"ts": now(), "task": task["id"], "attempt": attempt, "worker_rc": w.get("rc"), "check_rc": res["rc"],
               "sig": res["sig"], "secs": res["secs"], "tamper": tamper2, "passed": passed,
               "files": G.changed_files(proj.rundir)}
        task["history"].append({**rec, "out_tail": err_text(task, res) if not passed else ""})
        task["status"] = "done" if passed else "pending"
        if passed: task["commit"] = G.checkpoint(proj, task)
        append_jsonl(proj.p("attempts.jsonl"), rec)
        stuck = [] if passed else B.signals(proj, task)
        if stuck:   # interim hand-off (Step G replaces this with the full ladder): never loop forever
            task["status"] = "blocked"
            task["blocked_question"] = "Stuck (" + "; ".join(m for _, m in stuck) + "). Last error: " + err_text(task, res)[-300:].replace("\n", " ")
            append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "harness", "op": "block", "ok": True,
                         "reason": task["blocked_question"][:300], "before": [(task["id"], "pending")], "after": [(task["id"], "blocked")]})
        store.save(data)
    if stuck:
        return {"event": "task_blocked", "task": task["id"], "attempt": attempt, "signals": [n for n, _ in stuck],
                "question": task["blocked_question"], "tamper": tamper2}
    return {"event": "task_done" if passed else "task_failed", "task": task["id"], "attempt": attempt, "fresh": fresh,
            "check_rc": res["rc"], "tamper": tamper2, "out_tail": res["out"][-400:]}

def reset(proj):
    """Manual/ladder primitive: code dir back to the last verified checkpoint."""
    G.ensure_baseline(proj); return G.reset_to_last_good(proj)
