"""Task-, call- and project-level metrics for one trial, read from the harness's own records (tasks.json, taskclock.json, attempts.jsonl, trace.jsonl,
plan.log, audit.jsonl, thermal.jsonl). Nothing here is asked of the model. Raw evidence is copied next to the result so classifications can be revised later."""
import json, os, re, shutil, statistics
from .. import taskclock as TC, tasks as T

def jsonl(path):
    out = []
    try:
        with open(path) as f:
            for ln in f:
                try: out.append(json.loads(ln))
                except ValueError: pass
    except OSError: pass
    return out

STATUS = {"done": "VERIFIED", "timeout": "TIMEOUT", "skipped": "SKIPPED", "blocked": "BLOCKED", "pending": "INCOMPLETE", "running": "INCOMPLETE"}
ESC = re.compile(r"^(T[\d.]+): rung (\d) \(([\w-]+)\) -> (\d) \(([\w-]+)\): (.*)$")

def _family(tasks, root): return [t for t in tasks if TC.root_of(t) == root]

def collect_tasks(proj):
    """One record per ROOT task (a split task and its children are one unit of work with one budget)."""
    store = T.TaskStore(proj)
    if not store.exists(): return {"tasks": [], "frozen": False}
    tasks = store.load()["tasks"]; clocks = TC.summary(proj)
    atts = [a for a in jsonl(proj.p("attempts.jsonl")) if "attempt" in a and "task" in a]
    events = jsonl(proj.p("attempts.jsonl")); trace = jsonl(proj.p("trace.jsonl")); plan_log = jsonl(proj.p("plan.log"))
    roots = []
    for t in tasks:
        r = TC.root_of(t)
        if r not in roots: roots.append(r)
    out = []
    for root in roots:
        fam = _family(tasks, root); ids = {t["id"] for t in fam}; primary = next((t for t in fam if t["id"] == root), fam[0])
        mine = [a for a in atts if a["task"] in ids]; turns = [e for e in trace if e.get("event") == "turn" and e.get("task") in ids]
        ends = [e for e in trace if e.get("event") == "attempt_end" and e.get("task") in ids]
        clk = clocks.get(root, {}); status = STATUS.get(primary["status"], "INCOMPLETE")
        esc = []
        for l in plan_log:
            if l.get("op") == "escalate":
                m = ESC.match(l.get("reason", ""))
                if m and m.group(1) in ids: esc.append({"task": m.group(1), "from": int(m.group(2)), "to": int(m.group(4)), "from_name": m.group(3), "to_name": m.group(5), "why": m.group(6)})
        splits = sorted({k for t in fam for k in (t.get("split_into") or [])})
        last_hist = next((h for t in fam for h in reversed(t.get("history") or []) if h.get("out_tail")), None)
        calls = len(turns)
        out.append({
            "id": root, "title": primary["title"], "size": primary.get("size"), "deps": primary.get("deps", []), "files": primary.get("files", []),
            "status": status, "engine_status": primary["status"], "family": sorted(ids), "split_into": splits, "added_by_model": [t["id"] for t in fam if t.get("budget_root") and t["id"] != root],
            "timing": {"used_s": clk.get("used_s", 0.0), "limit_s": clk.get("limit_s"), "remaining_s": clk.get("remaining_s"), "thermal_s": clk.get("thermal_s", 0.0), "steps": clk.get("steps", 0),
                       "charged_after_crash_s": clk.get("charged_after_crash_s", 0.0), "first_start": clk.get("first_start"), "last_end": clk.get("last_end")},
            "model": {"calls": calls, "inference_s": round(sum(e.get("secs") or 0 for e in turns), 1), "prompt_tokens": sum(e.get("ptok") or 0 for e in turns), "gen_tokens": sum(e.get("gtok") or 0 for e in turns),
                      "prompt_ms": sum(e.get("prompt_ms") or 0 for e in turns), "gen_ms": sum(e.get("gen_ms") or 0 for e in turns),
                      "malformed": sum(1 for e in turns if e.get("kind") == "malformed"), "truncated_replies": sum(1 for e in turns if e.get("kind") == "truncated"), "action_errors": sum(1 for e in turns if e.get("kind") == "action_error")},
            "attempts": len(mine), "retries": max(0, len(mine) - 1), "max_rung": max([a.get("rung", 1) for a in mine] or [1]),
            "attempt_rows": [{"task": a["task"], "attempt": a["attempt"], "rung": a.get("rung"), "worker_secs": a.get("worker_secs"), "check_secs": a.get("secs"), "passed": a.get("passed"), "check_rc": a.get("check_rc"), "sig": a.get("sig"), "files": len(a.get("files") or {})} for a in mine],
            "ladder": esc, "ended_reasons": sorted({e.get("ended", "") for e in ends if e.get("ended") and e.get("ended") != "finished"}),
            "truncated_contexts": sum(e.get("truncated", 0) for e in ends),
            "tamper_events": sum(1 for e in events if e.get("event") == "tamper" and e.get("task") in ids),
            "internal": {"verified": primary["status"] == "done", "checks_passed": sum(1 for a in mine if a.get("passed")), "checks_failed": sum(1 for a in mine if not a.get("passed")),
                         "last_failure_tail": (last_hist or {}).get("out_tail", "")[-500:] if primary["status"] != "done" else ""},
            "blocked_question": primary.get("blocked_question"), "skip_reason": primary.get("skip_reason"), "timeout_reason": next((t.get("timeout_reason") for t in fam if t.get("timeout_reason")), None)})
    return {"tasks": out, "frozen": True}

def project_totals(tasks, proj, plan_secs=None):
    n = len(tasks); c = lambda s: sum(1 for t in tasks if t["status"] == s)
    verified, timeouts, blocked, skipped, incomplete = c("VERIFIED"), c("TIMEOUT"), c("BLOCKED"), c("SKIPPED"), c("INCOMPLETE")
    inf = sum(t["model"]["inference_s"] for t in tasks); interventions = sum(1 for l in jsonl(proj.p("plan.log")) if l.get("actor") == "human")
    return {"tasks_total": n, "verified": verified, "timeouts": timeouts, "blocked": blocked, "skipped": skipped, "incomplete": incomplete,
            "internal_success_rate": round(verified / n, 4) if n else 0.0, "inference_s": round(inf, 1), "model_calls": sum(t["model"]["calls"] for t in tasks),
            "prompt_tokens": sum(t["model"]["prompt_tokens"] for t in tasks), "gen_tokens": sum(t["model"]["gen_tokens"] for t in tasks),
            "task_time_s": round(sum(t["timing"]["used_s"] for t in tasks), 1), "thermal_wait_s": round(sum(t["timing"]["thermal_s"] for t in tasks), 1),
            "retries": sum(t["retries"] for t in tasks), "splits": sum(len(t["split_into"]) > 0 for t in tasks), "ladder_steps": sum(len(t["ladder"]) for t in tasks),
            "manual_interventions": interventions}

def planning_stats(proj, t_start_iso, t_end_iso):
    """Model calls made by the planner (audit.jsonl model_request entries inside the planning window)."""
    rows = [e for e in jsonl(proj.p("audit.jsonl")) if e.get("kind") == "model_request" and t_start_iso <= e.get("ts", "") <= t_end_iso]
    return {"calls": len(rows), "inference_s": round(sum(e.get("secs") or 0 for e in rows), 1), "prompt_tokens": sum(e.get("prompt_tokens") or 0 for e in rows), "gen_tokens": sum(e.get("gen_tokens") or 0 for e in rows)}

def model_calls(proj):
    """Every worker model invocation (trace.jsonl turn events), for per-call analysis."""
    return [{k: e.get(k) for k in ("ts", "task", "attempt", "n", "kind", "action", "ptok", "gtok", "prompt_ms", "gen_ms", "load_ms", "secs")} for e in jsonl(proj.p("trace.jsonl")) if e.get("event") == "turn"]

EVIDENCE = ("tasks.json", "taskclock.json", "attempts.jsonl", "trace.jsonl", "plan.log", "thermal.jsonl", "PLAN.md", "SPEC.md", "plan-auto.log", "loop.log", "ANSWERS.md")

def save_evidence(proj, dest):
    """Copy the raw records a classification was derived from (so it can be re-derived later); also the frozen assertion specs."""
    os.makedirs(dest, exist_ok=True)
    for fn in EVIDENCE:
        p = proj.p(fn)
        if os.path.isfile(p) and os.path.getsize(p) < 40 * 1024 * 1024: shutil.copyfile(p, os.path.join(dest, fn))
    cd = proj.p("checks")
    if os.path.isdir(cd):
        os.makedirs(os.path.join(dest, "checks"), exist_ok=True)
        for fn in os.listdir(cd):
            if fn.endswith(".spec.json") or fn.endswith(".sh"): shutil.copyfile(os.path.join(cd, fn), os.path.join(dest, "checks", fn))
