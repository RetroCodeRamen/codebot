"""Baseline guard for acceptance checks. A check that already passes against the current code tests nothing; one that is
broken (syntax error, command not found) tests nothing either. Both are rejected before the check is frozen, and every
rejection is logged to plan.log (op `check_rejected`) so the planner's mistakes can be counted."""
import os, re, subprocess, tempfile
from . import checks as C, sandbox
from .config import now, append_jsonl

class GuardError(Exception):
    pass

def enabled(proj): return str(proj.get("CHECK_BASELINE_GUARD", "0")) not in ("0", "", "false", "no")

def static_flaw(proj, check_text):
    """Mistakes a real 9B planner made that the shell does not report as an error (the check just fails forever, which looks like a good failing check):
    a backtick inside the command (command substitution), an angle-bracket <placeholder> (a redirect), a missing $CODEBOT_CHECKS script, and a script that
    hardcodes the project's state directory (the code is not there; checks run in the code directory). Returns a message or None."""
    if "`" in check_text: return "the command contains a backtick, which the shell runs as a command substitution; write the command itself with no backticks inside the outer backticks"
    if re.search(r"<[^<>\s]*[/.$][^<>\s]*>", check_text) or re.search(r"<[A-Za-z_ -]+>", check_text):
        return "the command contains an angle-bracket <placeholder>, which the shell reads as a redirect; write the real command, for example python3 $CODEBOT_CHECKS/t1_test.py"
    if re.search(r"(?<![\w$}])(?:\S*/)?checks/[\w.\-]+", re.sub(r"\$\{?CODEBOT_CHECKS\}?/[\w.\-]+", "", check_text)):
        return "the command names a script under checks/ by a relative or absolute path, but checks run inside the code directory where that path does not exist; use $CODEBOT_CHECKS/<name>"
    if re.search(r"\bpython3?\s+\S+\.sh\b", check_text) or re.search(r"\b(?:ba)?sh\s+\S+\.py\b", check_text):
        return "a .sh script must be run with bash and a .py script with python3 (the command runs one with the other's interpreter)"
    for m in re.finditer(r"\$\{?CODEBOT_CHECKS\}?/([\w.\-]+)", check_text):
        path = os.path.join(C.checks_dir(proj), m.group(1))
        if not os.path.isfile(path): return f"the script {m.group(1)} does not exist in the checks directory; write it there first"
        try:
            with open(path, errors="replace") as f: body = f.read()
        except OSError: continue
        if proj.state and proj.state in body:
            return f"{m.group(1)} hardcodes the project state directory, but checks run inside the code directory; use relative paths (it starts in the code directory)"
    return None

def probe(proj, check_text, timeout=120):
    """Run one check command against the CURRENT code, read-only. Returns dict(verdict, rc, out): verdict is
    'ok' (fails now, for a real reason), 'passes' (exit 0: tests nothing) or 'broken'."""
    flaw = static_flaw(proj, check_text)
    if flaw: return {"verdict": "broken", "rc": 2, "out": flaw}
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
        if t.get("check_note"):
            res.append({"id": t["id"], "title": t["title"], "check": t["check"], "verdict": "malformed", "rc": None, "out": f"text after the closing backtick: {t['check_note'][:120]!r}"}); continue
        p = probe(proj, t["check"], proj.get("CHECK_TIMEOUT", 600, int))
        res.append({"id": t["id"], "title": t["title"], "check": t["check"], **p})
    return res

REASON = {"malformed": "the check line has text AFTER the closing backtick; a check is ONE shell command and nothing else, so write only the command inside the backticks",
          "passes": "the check already PASSES against the current code, so it tests nothing. Rewrite it so it fails until the task is really done",
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
