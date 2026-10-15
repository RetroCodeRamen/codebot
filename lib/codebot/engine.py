"""The engine: one `step` = one attempt at the next task, decided and judged by the harness, not the model."""
import json, os, re, subprocess, sys, time
from . import taskclock as TC, tasks as T, checks as C, gitops as G, budgets as B, sizing as Z, ladder as L, memory as M, modes as MD, guard as GD
from .config import Project, now, append_jsonl
from .proc import kill_group
from .planparse import parse_plan

def freeze_project(proj, allow_unchecked=False, allow_passing=False):
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
    try: MD.copy_verifiers(proj)                 # the mode's verifier helpers become part of the frozen checks
    except MD.ModeError as e: raise SystemExit(f"{proj.name}: {e}")
    if GD.enabled(proj) and not allow_passing:   # a check that already passes (or is broken) tests nothing: reject before freezing
        try: GD.enforce(proj, tasks)
        except GD.GuardError as e: raise SystemExit(f"{proj.name}: {e}")
    with store.locked():
        C.freeze(proj, data); store.save(data)
    append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "harness", "op": "freeze", "reason": "initial freeze",
                                       "tasks": len(tasks)})
    return data

LIBDIR = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

def _worker_cmd(proj):
    """WORKER_CMD overrides; WORKER=native runs the built-in constrained-output worker."""
    cmd = proj.get("WORKER_CMD")
    if cmd: return cmd
    if proj.get("WORKER") == "native": return f"{sys.executable} -m codebot.worker"
    return None

def kill_sandbox_containers(proj):
    """A killed worker may leave a sandbox container running to its own timeout; containers carry the project label."""
    try:
        ids = subprocess.run(["docker", "ps", "-q", "--filter", f"label=codebot.project={proj.name}"], capture_output=True, text=True, timeout=20).stdout.split()
        if ids: subprocess.run(["docker", "kill", *ids], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError): pass

def run_worker(proj, task, attempt, last_error, extra_env=None, timeout=None):
    """Pluggable worker. Contract: runs in the code dir, gets the task via env/file, may edit code; its exit
    status and any claim of success are ignored: only the frozen check decides. `timeout` (seconds) is enforced by killing the whole process group."""
    cmd = _worker_cmd(proj)
    if not cmd: return {"rc": None, "note": "no WORKER_CMD configured"}
    tfile = proj.p(".current_task.json"); C.jdump(tfile, task, indent=1)
    efile = proj.p(".last_error.txt")
    with open(efile, "w") as f: f.write(last_error or "")
    env = dict(os.environ, CODEBOT_TASK_FILE=tfile, CODEBOT_TASK_ID=task["id"], CODEBOT_ATTEMPT=str(attempt),
               CODEBOT_LAST_ERROR_FILE=efile, CODEBOT_RUNDIR=proj.rundir, CODEBOT_STATE=proj.state,
               CODEBOT_CHECKS=C.checks_dir(proj), CODEBOT_ANSWERS_FILE=proj.p("ANSWERS.md"), CODEBOT_HOME=proj.home,
               CODEBOT_PROJECT=proj.name, PYTHONPATH=LIBDIR + os.pathsep + os.environ.get("PYTHONPATH", ""), **(extra_env or {}))
    t0 = time.time(); limit = timeout if timeout is not None else proj.get("ITER_TIMEOUT", 2700, int)
    proc = subprocess.Popen(cmd, shell=True, cwd=proj.rundir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=limit)
        return {"rc": proc.returncode, "secs": round(time.time() - t0, 1), "tail": (out or "")[-800:]}
    except subprocess.TimeoutExpired:
        kill_group(proc); kill_sandbox_containers(proj)
        return {"rc": 124, "secs": round(time.time() - t0, 1), "tail": "[worker timed out]"}

def err_text(task, res):
    """What the worker is told about a failing check. Silent checks (grep -q, test -f) get a description."""
    out = (res.get("out") or "").strip()
    if out: return out[-1500:]
    return f"The acceptance check `{task.get('check')}` exited with status {res['rc']} and printed nothing. It is run in the code directory."

def _block(proj, task, why_text, res):
    task["status"] = "blocked"; task["blocked_question"] = L.question(proj, task, why_text)
    Z.record(proj, task, "blocked")
    append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "harness", "op": "block", "ok": True,
                 "reason": task["blocked_question"][:400], "before": [(task["id"], "pending")], "after": [(task["id"], "blocked")]})

def block_terminal(proj): return str(proj.get("BLOCK_TERMINAL", "0")).lower() in ("1", "true", "yes")

def _expire_exhausted(proj, data):
    """Tasks whose root budget is already used up (e.g. after a restart) end as `timeout` before anything runs. Returns the ids changed."""
    lim = TC.limit_s(proj)
    if not lim: return []
    out = []
    for t in data["tasks"]:
        if t["status"] in ("pending", "running") and TC.used(proj, TC.root_of(t)) >= lim:
            t["status"] = "timeout"; t["timeout_reason"] = f"the {int(lim // 60)} min budget of {TC.root_of(t)} was already used up"; out.append(t["id"])
    return out

def _time_out(proj, store, data, task, clk, why):
    """The root task's budget ran out: every unfinished task of that root ends as `timeout`, dependents become `skipped`, and the code goes back
    to the last verified checkpoint so later tasks (and the final reference check) see a consistent tree. Caller holds the store lock."""
    root = TC.root_of(task); fam = [t for t in data["tasks"] if TC.root_of(t) == root and t["status"] in ("pending", "running")]
    for t in fam: t["status"] = "timeout"; t["timeout_reason"] = why
    skipped = T.resolve_skips(data, block_terminal(proj))
    G.reset_to_last_good(proj); store.save(data)
    used = round(clk.used(), 1) if clk else None
    append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "harness", "op": "timeout", "ok": True, "reason": f"{root}: {why}",
                 "before": [(t["id"], "pending") for t in fam], "after": [(t["id"], "timeout") for t in fam]})
    append_jsonl(proj.p("attempts.jsonl"), {"ts": now(), "event": "timeout", "task": task["id"], "root": root, "used_s": used, "skipped": skipped})
    TC.mark(proj, root, "timeout", clk.remaining() if clk else None)
    return {"event": "task_timeout", "task": task["id"], "root": root, "used_s": used, "limit_s": TC.limit_s(proj), "unfinished": [t["id"] for t in fam], "skipped": skipped}

def step(proj, fresh=False):
    """One attempt at the next task (see _step). The task's budget interval is always closed, even when the step raises."""
    box = {"clk": None}
    try: return _step(proj, fresh, box)
    finally:
        if box["clk"]: box["clk"].end()

def _step(proj, fresh, box):
    """One attempt at the next task. Returns an event dict for the supervisor (the bash loop) to act on.
    fresh=True forces a reset to the last verified checkpoint before the worker runs (the ladder does this at rung 2)."""
    store = T.TaskStore(proj); TC.settle(proj)
    if not store.exists(): return {"event": "error", "error": "not frozen: run `codebot freeze`"}
    if os.path.realpath(proj.rundir).startswith(os.path.realpath(proj.state) + os.sep):
        return {"event": "error", "error": f"the code directory ({proj.rundir}) is inside the harness state directory, so the worker could touch nothing. "
                                            f"Run `codebot start {proj.name}` once to migrate the worktree out."}
    over = B.project_budget(proj)
    if over: return {"event": "budget", "kind": over[0], "message": over[1]}
    with store.locked():
        tamper = [] if store.check_integrity() else ["tasks.json:edited-outside-engine"]
        data = store.load()
        tamper += C.verify(proj, data) if C.is_frozen(proj) else []
        if tamper:
            C.restore(proj, data, tamper)
            append_jsonl(proj.p("attempts.jsonl"), {"ts": now(), "event": "tamper", "findings": tamper})
        for t in data["tasks"]:        # a previous step was killed (pause/crash): that attempt never finished, do not charge it
            if t["status"] == "running":
                t["status"] = "pending"; t["attempts"] = max(0, t["attempts"] - 1)
                append_jsonl(proj.p("attempts.jsonl"), {"ts": now(), "event": "interrupted", "task": t["id"]})
        _expire_exhausted(proj, data); T.resolve_skips(data, block_terminal(proj))
        task = T.next_task(data)
        if task is None:
            c = T.counts(data); store.save(data)
            if c["pending"] == 0 and c["running"] == 0:
                if c["blocked"] == 0 and not c["timeout"] and not c["skipped"]: return {"event": "all_done", "counts": c, "tamper": tamper}
                if c["blocked"] and not block_terminal(proj) and not c["timeout"] and not c["skipped"]: return {"event": "done_with_blocked", "counts": c, "tamper": tamper}
                return {"event": "finished_incomplete", "counts": c, "tamper": tamper}     # some tasks ended as timeout/skipped (or a block is terminal): nothing more can happen
            return {"event": "stalled", "counts": c, "tamper": tamper, "note": "remaining tasks wait on blocked ones"}
        if not task.get("check") and not proj.get("ALLOW_UNCHECKED", None, str):
            return {"event": "error", "task": task["id"], "error": "task has no check"}
        G.ensure_baseline(proj); box["clk"] = TC.begin(proj, task); clk = box["clk"]
        do_fresh, extra_env = L.mods(proj, task)
        if fresh or do_fresh: G.reset_to_last_good(proj)
        # Run the check BEFORE the worker: if it already passes there is nothing to do (the final gate of a split task,
        # or a weak check), and if it fails its output is the freshest, most accurate error for the worker.
        ct = proj.get("CHECK_TIMEOUT", 600, int)
        pre = C.run_check(proj, task, clk.cap(ct) if clk else ct) if task.get("check") else None
        if pre is not None and pre["rc"] == 0:
            task["status"] = "done"; task["commit"] = G.checkpoint(proj, task)
            task["history"].append({"ts": now(), "precheck_pass": True, "gate": bool(task.get("split_into"))})
            if not task.get("split_into"): Z.record(proj, task, "done")
            store.save(data)
            append_jsonl(proj.p("attempts.jsonl"), {"ts": now(), "task": task["id"], "event": "precheck_pass", "gate": bool(task.get("split_into"))})
            if not task.get("split_into"):
                append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "harness", "op": "weak_check", "ok": True,
                             "reason": f"{task['id']} check passed before any work"})
            return {"event": "task_done", "task": task["id"], "attempt": task["attempts"], "precheck": True, "tamper": tamper}
        if clk and clk.expired(): return _time_out(proj, store, data, task, clk, f"the {int(clk.lim // 60)} min budget ran out before an attempt could start")
        task["status"] = "running"; task["attempts"] += 1; attempt = task["attempts"]
        store.save(data)
        last = err_text(task, pre) if pre else ""
    try: os.remove(proj.p("packed.json"))
    except OSError: pass
    w = run_worker(proj, task, attempt, last, extra_env, timeout=clk.cap(proj.get("ITER_TIMEOUT", 2700, int)) if clk else None)
    need_split = False
    with store.locked():
        tamper2 = [] if store.check_integrity() else ["tasks.json:edited-outside-engine"]
        data = store.load(); task = T.get(data, task["id"])
        tamper2 += C.verify(proj, data) if C.is_frozen(proj) else []
        if tamper2:
            C.restore(proj, data, tamper2)
            append_jsonl(proj.p("attempts.jsonl"), {"ts": now(), "event": "tamper", "task": task["id"], "findings": tamper2})
        if clk and clk.expired() and w.get("rc") == 124:       # the worker was killed because the budget ran out: nothing more to judge
            append_jsonl(proj.p("attempts.jsonl"), {"ts": now(), "task": task["id"], "attempt": attempt, "event": "attempt_cut_by_budget", "worker_secs": w.get("secs")})
            return _time_out(proj, store, data, task, clk, f"the {int(clk.lim // 60)} min budget ran out during attempt {attempt}")
        res = C.run_check(proj, task, clk.cap(proj.get("CHECK_TIMEOUT", 600, int)) if clk else proj.get("CHECK_TIMEOUT", 600, int)) if task.get("check") else {"rc": 0, "out": "", "sig": "", "secs": 0}
        passed = res["rc"] == 0 and not tamper2
        rec = {"ts": now(), "task": task["id"], "attempt": attempt, "rung": L.rung_of(task), "worker_rc": w.get("rc"), "worker_secs": w.get("secs"),
               "check_rc": res["rc"], "sig": res["sig"], "secs": res["secs"], "tamper": tamper2, "passed": passed,
               "files": G.changed_files(proj.rundir)}
        packed = []                                    # lessons the worker's context actually contained this attempt
        try:
            with open(proj.p("packed.json")) as f: packed = json.load(f).get("lessons", [])
        except (OSError, ValueError): pass
        rec["lessons"] = packed
        M.record_outcome(proj.home, packed, passed); M.prune(proj.home)
        append_jsonl(proj.p("attempts.jsonl"), rec)
        task["history"].append({**{k: v for k, v in rec.items() if k != "files"}, "out_tail": err_text(task, res) if not passed else ""})
        action, signals, new_rung, why = None, [], None, ""
        if passed:
            task["status"] = "done"; task["commit"] = G.checkpoint(proj, task); Z.record(proj, task, "done")
        else:
            task["status"] = "pending"
            signals = [n for n, _ in B.signals(proj, task)]
            action, new_rung, why = L.decide(proj, task, signals)
            old = L.rung_of(task)
            if action != "stay":
                L.log_escalation(proj, task, old, new_rung, why); task["ladder"] = new_rung; task["rung_tries"] = 0
            if new_rung == 3 and not task.get("split_into"): need_split = True
            if action == "block": _block(proj, task, err_text(task, res), res)
        if not passed and clk and clk.expired():
            return _time_out(proj, store, data, task, clk, f"the {int(clk.lim // 60)} min budget ran out while attempt {attempt} was being judged")
        store.save(data)
    if need_split:     # split needs the store lock itself, so it runs after we release it
        r = Z.auto_split(proj, task["id"], err_text(task, res), timeout=clk.cap(1800) if clk else 1800)
        if r["ok"]: return {"event": "task_split", "task": task["id"], "children": r["children"], "attempt": attempt, "tamper": tamper2}
        with store.locked():                                   # no split available: skip straight to the next rung
            data = store.load(); t2 = T.get(data, task["id"]); nr = L.next_rung(proj, 3)
            L.log_escalation(proj, t2, 3, nr, "no split available"); t2["ladder"] = nr; t2["rung_tries"] = 0
            if nr >= 6:
                _block(proj, t2, err_text(task, res), res); action = "block"; task = t2
            store.save(data)
    if action == "block":
        return {"event": "task_blocked", "task": task["id"], "attempt": attempt, "signals": signals, "why": why,
                "question": task["blocked_question"], "tamper": tamper2}
    return {"event": "task_done" if passed else "task_failed", "task": task["id"], "attempt": attempt, "rung": L.rung_of(task),
            "fresh": fresh, "check_rc": res["rc"], "tamper": tamper2, "out_tail": res["out"][-400:]}

def answer(proj, text, task_id=None):
    """A human answer: recorded, and the (named or first) blocked task goes back to rung 1 with a fresh budget."""
    m = re.match(r"^\s*(T\d+(?:\.\d+)*)\s*[:,\-]\s*(.*)$", text, re.S)
    if m and not task_id: task_id, text = m.group(1), m.group(2)
    store = T.TaskStore(proj); released = None
    if store.exists():
        with store.locked():
            store.check_integrity(); data = store.load()
            blocked = [t for t in data["tasks"] if t["status"] == "blocked"]
            tgt = T.get(data, task_id) if task_id else (blocked[0] if blocked else None)
            if tgt is not None and tgt["status"] == "blocked":
                tgt["status"] = "pending"; tgt["ladder"] = 1; tgt["rung_tries"] = 0; tgt["attempts_base"] = tgt["attempts"]
                q, tgt["blocked_question"] = tgt["blocked_question"], None; released = tgt["id"]
                store.save(data)
                append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "human", "op": "answer", "ok": True,
                             "reason": f"{released} unblocked", "before": [(released, "blocked")], "after": [(released, "pending")]})
    with open(proj.p("ANSWERS.md"), "a") as f:
        f.write(f"\n## {now()} (answer{' to ' + (released or task_id) if (released or task_id) else ''})\n{text.strip()}\n")
    return {"ok": True, "released": released}

def reset(proj):
    """Manual/ladder primitive: code dir back to the last verified checkpoint."""
    G.ensure_baseline(proj); return G.reset_to_last_good(proj)
