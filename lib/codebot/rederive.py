"""Before a failing task is split, ask whether its check is wrong. The ladder used to assume a failing check means wrong code; in the calc_go control
rerun (2026-10-17) 38 of 41 frozen checks could not pass for ANY correct program, and splitting copied them into ever smaller tasks. Here, when
some assertions failed in every attempt at a task, one model call (the planner's model) judges each of those assertions against the spec text
and may propose replacements. The harness then decides:
  - only assertions that failed in EVERY attempt may go; the spec's own examples (grounding.py) never go; assertions that ever passed stay;
  - replacements are screened like any planned assertion (grounding.screen, assertions.validate) and must not repeat a removed one;
  - the new check may not be broken; it replaces the task's check as a new frozen spec file (the old one stays on disk and in the log);
  - at most REDERIVE_MAX times per task (default 1); the task's ladder starts again at rung 1 with the new check.
When nothing changes, the split goes ahead with the persistent assertions passed on as `avoid`, so no child copies them."""
import json, re
from . import assertions as A, checks as C, checkspec as K, grounding as G, guard, ollama, roles, specdoc, tasks as T
from .config import now, append_jsonl

FAILING = re.compile(r"^failing assertions: ([\d, ]+)\s*$", re.M)
MIN_SECS = 120                        # the judging call needs this much of the task's remaining budget (about 300 tokens at 6-9 tokens/s, plus the prompt)

def failing_numbers(out):
    """1-based numbers of the assertions the runner reported failing (its `failing assertions:` line), or None when the output has none."""
    m = None
    for m in FAILING.finditer(out or ""): pass
    return [int(x) for x in m.group(1).replace(" ", "").split(",") if x] if m else None

def persistent(task, min_attempts=2):
    """Numbers of the assertions that failed in every attempt at the task's current check (attempts since attempts_base that recorded `failing`)."""
    base = task.get("attempts_base", 0)
    hist = [h for h in task.get("history", []) if isinstance(h.get("attempt"), int) and h["attempt"] > base and isinstance(h.get("failing"), list)]
    if len(hist) < min_attempts: return []
    common = set(hist[0]["failing"])
    for h in hist[1:]: common &= set(h["failing"])
    return sorted(common)

def schema():
    return {"type": "object", "properties": {
        "verdicts": {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "integer"}, "required_by_spec": {"type": "boolean"},
                                                                               "why": {"type": "string", "maxLength": 240}},
                                                 "required": ["n", "required_by_spec", "why"], "additionalProperties": False}},
        "replacements": {"type": "array", "maxItems": 8, "items": K.assert_schema()["items"]}},
        "required": ["verdicts", "replacements"], "additionalProperties": False}

SYSTEM = "You check acceptance tests against a specification. You never write code. Reply with JSON only."

def prompt(spec_text, task, asserts, stuck, last_out, attempts):
    listing = "\n".join(f"{i}. {json.dumps(a)}" for i, a in enumerate(asserts, 1))
    fails = "\n".join(l for l in (last_out or "").splitlines() if l.startswith("FAIL: "))[-1500:]
    nums = ", ".join(map(str, stuck))
    return (f"SPECIFICATION (the part this task implements):\n{spec_text}\n\nTASK: {task['title']}\n\n"
            "The task's acceptance test is this list of assertions. A trusted runner evaluates them in the code directory: `call` imports a Python module and "
            "calls a function; `run` starts a program with exactly the given argv (no shell: no pipes, no echo, no redirection), feeds `stdin` to its standard "
            "input, and compares its exit status (0 unless `exit` is given) and its standard output; standard error is not compared.\n"
            f"{listing}\n\nThe worker failed assertion(s) {nums} in every one of its {attempts} attempts. The last failures were:\n{fails or '(no detail)'}\n\n"
            f"For each of the assertions {nums}, decide: does the SPECIFICATION require exactly this result for exactly this input? Answer false when the "
            "specification states a different result, does not define this input, or when no program that follows the specification could pass it (for "
            "example an option or command the specification never mentions, standard output the specification sends to standard error, a Python call in a "
            "project that is not written in Python). The program may simply be wrong: answer true whenever the specification does require it. Then give "
            "replacements: assertions that test the same requirement correctly, using only inputs and results the specification states (or an empty list).")

def run(proj, tid, last_out="", timeout=900):
    """Re-derive the check of task tid when assertions failed in every attempt. Returns {"changed": bool, "avoid": [assertions], ...}."""
    store = T.TaskStore(proj); data = store.load(); task = T.get(data, tid)
    if task is None or not K.is_runner_check(task.get("check")): return {"changed": False, "avoid": [], "why": "not a declarative check"}
    asserts = K.read_task_spec(proj, task) or []; stuck = [n for n in persistent(task) if 1 <= n <= len(asserts)]
    avoid = [asserts[n - 1] for n in stuck]
    if not stuck: return {"changed": False, "avoid": [], "why": "no assertion failed in every attempt"}
    if task.get("rederived", 0) >= proj.get("REDERIVE_MAX", 1, int): return {"changed": False, "avoid": avoid, "why": "re-derivation limit reached (REDERIVE_MAX)"}
    spec = specdoc.load(proj)
    protected = {K.akey(e) for e in G.all_examples(spec)} if spec else set()          # the spec's own examples are never in question
    judged = [n for n in stuck if K.akey(asserts[n - 1]) not in protected]
    if not judged: return {"changed": False, "avoid": avoid, "why": "the persistent failures are the specification's own examples"}
    attempts = len([h for h in task.get("history", []) if isinstance(h.get("attempt"), int) and h["attempt"] > task.get("attempts_base", 0)])
    log = {"ts": now(), "actor": "harness", "op": "check_rederived", "task": tid, "stuck": stuck}
    if timeout < MIN_SECS:            # the call is paid from the task's own budget: with little left, the split (which still avoids them) is cheaper
        append_jsonl(proj.p("plan.log"), {**log, "ok": False, "reason": f"only {int(timeout)} s left in the task's budget"})
        return {"changed": False, "avoid": avoid, "why": "too little time left"}
    try:
        r = ollama.chat(proj, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt(specdoc.for_task(proj, task), task, asserts, judged, last_out, attempts)}],
                        schema=schema(), temperature=0.2, num_predict=2500, timeout=int(timeout), retries=1,
                        model=roles.model(proj, "planner"), think=roles.think(proj, "planner"), role="planner")
        reply = json.loads(r.get("content") or "")
    except (ollama.ModelError, ValueError) as e:
        append_jsonl(proj.p("plan.log"), {**log, "ok": False, "reason": f"no usable reply: {type(e).__name__}: {str(e)[:200]}"})
        return {"changed": False, "avoid": avoid, "why": "no usable reply"}
    with open(proj.p(f"rederive.reply.{tid}.json"), "w") as f: json.dump(reply, f, indent=1)
    wrong = {v["n"]: v.get("why", "") for v in reply.get("verdicts") or [] if isinstance(v, dict) and v.get("n") in judged and v.get("required_by_spec") is False}
    if not wrong:
        append_jsonl(proj.p("plan.log"), {**log, "ok": False, "reason": "every assertion that kept failing was judged to be required by the specification"})
        return {"changed": False, "avoid": avoid, "why": "judged required"}
    kept = [a for i, a in enumerate(asserts, 1) if i not in wrong]; removed = [asserts[i - 1] for i in sorted(wrong)]
    reps, notes = _replacements(proj, task, data, spec, reply.get("replacements"), kept, removed)
    new = G.with_build(kept + reps, spec) if spec else kept + reps
    if A.validate(new):
        append_jsonl(proj.p("plan.log"), {**log, "ok": False, "reason": "removing the wrong assertions would leave no usable check", "wrong": wrong, "notes": notes[:10]})
        return {"changed": False, "avoid": avoid, "why": "nothing usable left"}
    res = C.replace_check(proj, tid, new, f"{len(wrong)} assertion(s) that failed in every attempt were judged not to follow the specification",
                          extra={"ladder": 1, "rung_tries": 0, "attempts_base": task.get("attempts", 0), "rederived": task.get("rederived", 0) + 1})
    if not res["ok"]:
        append_jsonl(proj.p("plan.log"), {**log, "ok": False, "reason": res["error"], "notes": notes[:10]})
        return {"changed": False, "avoid": avoid, "why": res["error"]}
    append_jsonl(proj.p("plan.log"), {**log, "ok": True, "reason": f"{len(removed)} removed, {len(reps)} added", "removed": [{"assert": a, "why": wrong[i]} for i, a in zip(sorted(wrong), removed)],
                                      "added": reps, "notes": notes[:10], "check": res["check"]})
    return {"changed": True, "avoid": [], "removed": removed, "added": reps, "check": res["check"]}

def _replacements(proj, task, data, spec, raw, kept, removed):
    """The model's replacement assertions that survive cleaning, repair, validation and the grounding screen, minus repeats. Returns (list, notes)."""
    from .planner import salvage
    if not isinstance(raw, list) or not raw: return [], []
    groups, notes = K.repair_plan([K.prepare(raw)], [kept])
    cand, dropped = salvage(groups[0]); notes += [f"dropped a replacement: {e}" for e in dropped]
    gone = {K.akey(a) for a in removed} | {K.akey(a) for a in kept}
    cand = [a for a in cand if K.akey(a) not in gone]
    if spec and cand:
        ctx = list(kept) + [a for t in data["tasks"] if t["status"] == "done" and K.is_runner_check(t.get("check")) for a in (K.read_task_spec(proj, t) or [])]
        out, sn, _ = G.screen([cand], spec, G.project_kind(spec, proj.rundir), None, G.all_examples(spec), ctx); cand = out[0]; notes += sn
    return cand, notes
