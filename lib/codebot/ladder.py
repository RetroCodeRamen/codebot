"""Escalation ladder. Fixed sequence per task: retry with the error -> reset to last good + retry fresh -> split ->
alternate prompt/temperature -> (optional larger local model) -> blocked + ask the human. Blocked never stops the run."""
from .config import now, append_jsonl

NAMES = {1: "retry-with-error", 2: "reset-and-retry-fresh", 3: "split", 4: "alternate-prompt", 5: "larger-model", 6: "blocked"}
DEFAULT_TRIES = {1: 2, 2: 2, 3: 0, 4: 2, 5: 1}

def tries(proj):
    """LADDER_TRIES="2,2,0,2,1": attempts allowed at rungs 1..5 (rung 3 = split, never an attempt)."""
    raw = proj.get("LADDER_TRIES")
    out = dict(DEFAULT_TRIES)
    if raw:
        for i, v in enumerate(raw.replace(" ", "").split(","), 1):
            if i <= 5 and v.isdigit(): out[i] = int(v)
    return out

def has_big_model(proj): return bool(proj.get("ESCALATE_MODEL"))

def rung_of(task): return task.get("ladder") or 1

def mods(proj, task):
    """How the next attempt differs from a plain one: (fresh_reset, extra_env)."""
    r, env = rung_of(task), {"CODEBOT_RUNG": str(rung_of(task)), "CODEBOT_RUNG_NAME": NAMES.get(rung_of(task), "")}
    if r == 4:
        env.update(CODEBOT_VARIANT="alt", CODEBOT_TEMPERATURE=str(proj.get("ALT_TEMPERATURE", "0.8")))
    if r == 5 and has_big_model(proj):
        env.update(CODEBOT_MODEL=proj.get("ESCALATE_MODEL"), CODEBOT_VARIANT="alt")
    return r == 2, env

def next_rung(proj, rung):
    r = rung + 1
    if r == 5 and not has_big_model(proj): r = 6
    return r

def decide(proj, task, signal_names):
    """After a failed attempt. Returns (action, new_rung, why). action: stay | advance | block."""
    if "task_budget" in signal_names: return ("block", 6, "attempt cap reached")
    r = rung_of(task); task["rung_tries"] = task.get("rung_tries", 0) + 1
    exhausted = task["rung_tries"] >= tries(proj).get(r, 1)
    soft = [n for n in signal_names if n != "task_budget"]
    if soft or exhausted:
        why = ("stuck signal: " + ", ".join(soft)) if soft else f"{task['rung_tries']} attempts at rung {r} failed"
        nr = next_rung(proj, r)
        return ("block" if nr >= 6 else "advance", nr, why)
    return ("stay", r, "")

def log_escalation(proj, task, old, new, why):
    append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "harness", "op": "escalate", "ok": True,
                 "reason": f"{task['id']}: rung {old} ({NAMES.get(old)}) -> {new} ({NAMES.get(new)}): {why}",
                 "before": [(task["id"], old)], "after": [(task["id"], new)]})

def question(proj, task, last_error):
    tried = ", ".join(NAMES[i] for i in range(1, 6) if i != 5 or has_big_model(proj))
    return (f"{task['id']} \"{task['title']}\" failed through the whole ladder ({tried}). "
            f"Last error: {last_error[-300:].strip()}  "
            f"What should I do? Reply with: codebot answer {proj.name} \"{task['id']}: <your guidance>\"").replace("\n", " ")
