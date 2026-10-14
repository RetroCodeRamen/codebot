"""Baseline guard for acceptance checks. A check that already passes against the current code tests nothing; one that is
broken (syntax error, command not found) tests nothing either. Both are rejected before the check is frozen, and every
rejection is logged to plan.log (op `check_rejected`) so the planner's mistakes can be counted."""
import os, re, subprocess, tempfile
from . import checks as C, sandbox
from .config import now, append_jsonl

class GuardError(Exception):
    pass

def enabled(proj): return str(proj.get("CHECK_BASELINE_GUARD", "0")) not in ("0", "", "false", "no")

def probe(proj, check_text, timeout=120):
    """Run one check command against the CURRENT code, read-only. Returns dict(verdict, rc, out): verdict is
    'ok' (fails now, for a real reason), 'passes' (exit 0: tests nothing) or 'broken'."""
    script = f'cd "$CODEBOT_RUNDIR" || exit 99\n{check_text}\n'
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f: f.write(script); tmp = f.name
    try:
        syn = subprocess.run(["bash", "-n", tmp], capture_output=True, text=True)
        if syn.returncode: return {"verdict": "broken", "rc": 2, "out": "shell syntax error: " + syn.stderr.strip().replace(tmp, "check")[:300]}
        rc, out, _ = sandbox.execute(proj, ["bash", "-c", script], timeout=timeout, rw=(), ro=[proj.rundir, C.checks_dir(proj)],
                                     env={"CODEBOT_RUNDIR": proj.rundir, "CODEBOT_CHECKS": C.checks_dir(proj)})
    finally: os.unlink(tmp)
    out = out.strip()[-400:]
    if rc == 0: return {"verdict": "passes", "rc": 0, "out": out}
    if rc in (126, 127) or re.search(r"command not found|No such file or directory: .*\.sh", out): return {"verdict": "broken", "rc": rc, "out": out}
    return {"verdict": "ok", "rc": rc, "out": out}

def evaluate(proj, tasks):
    """[{id, title, check, verdict, rc, out}] for every task that still needs a check (done tasks are skipped)."""
    res = []
    for t in tasks:
        if t.get("status") == "done" or not (t.get("check") or "").strip(): continue
        p = probe(proj, t["check"], proj.get("CHECK_TIMEOUT", 600, int))
        res.append({"id": t["id"], "title": t["title"], "check": t["check"], **p})
    return res

REASON = {"passes": "the check already PASSES against the current code, so it tests nothing. Rewrite it so it fails until the task is really done",
          "broken": "the check is BROKEN, so it cannot test anything"}

def log_rejection(proj, tid, title, check, verdict, out, actor):
    append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": actor, "op": "check_rejected", "ok": False, "task": tid,
                 "reason": f"{tid}: {REASON.get(verdict, verdict)}", "verdict": verdict, "check": check[:300], "out": out[-200:]})

def message(proj, findings):
    L = [f"{len(findings)} check(s) rejected; nothing was frozen:"]
    for f in findings:
        L.append(f"  {f['id']} \"{f['title'][:60]}\": {REASON[f['verdict']]}.\n      check: {f['check'][:160]}" + (f"\n      output: {f['out'][-160:]}" if f["out"] else ""))
    L.append(f"Fix PLAN.md, then run `codebot check-plan {proj.name}` until it says OK (or `codebot freeze {proj.name} --allow-passing-checks` to override).")
    return "\n".join(L)

def enforce(proj, tasks, actor="harness"):
    """Raises GuardError (after logging each rejection) if any task's check passes now or is broken."""
    bad = [f for f in evaluate(proj, tasks) if f["verdict"] != "ok"]
    for f in bad: log_rejection(proj, f["id"], f["title"], f["check"], f["verdict"], f["out"], actor)
    if bad: raise GuardError(message(proj, bad))
