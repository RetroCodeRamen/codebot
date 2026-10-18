"""Native unattended planner. ONE constrained-output model call turns SPEC.md into a structured plan (tasks, files, sizes, deps and declarative
ASSERTIONS); the harness writes everything else: the spec files, the check commands, PLAN.md. Then the harness runs the baseline guard itself and, for
rejected tasks only, asks the model for corrected assertions with the exact reason (repair rounds). The model never writes a path, a shell command, an
interpreter or a file, so the mistakes the free-form opencode planner made (wrong paths, backticks, <placeholders>, scripts run with the wrong
interpreter, implementation written instead of tests) cannot occur. The interactive `codebot plan` (a human in the loop) is unchanged."""
import json, os, re, subprocess, sys, time
from . import assertions as A, checkspec as K, grounding as G, guard, ollama, packer, planparse, roles, specdoc
from .config import Project, now, append_jsonl

TASK_PROPS = lambda: {"title": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}, "size": {"enum": ["S", "M", "L"]},
                      "deps": {"type": "array", "items": {"type": "integer"}}, "asserts": K.assert_schema()}

def plan_schema(max_tasks=10, min_tasks=1):
    """min_tasks is enforced by constrained decoding (verified on this Ollama: asked for one item under minItems 4, it returned 5), so a plan cannot end
    after one task; ornith did that in 3 of 6 first rounds with an 8-item spec."""
    s = {"type": "string"}
    return {"type": "object", "properties": {"goal": s, "context": s, "done": s, "tasks": {"type": "array", "minItems": max(1, min(min_tasks, max_tasks)), "maxItems": max_tasks, "items": {
        "type": "object", "properties": TASK_PROPS(), "required": ["title", "files", "size", "asserts"], "additionalProperties": False}}},
        "required": ["goal", "context", "done", "tasks"], "additionalProperties": False}

def task_schema():
    return {"type": "object", "properties": TASK_PROPS(), "required": ["title", "files", "size", "asserts"], "additionalProperties": False}

def repair_schema():
    return {"type": "object", "properties": {"tasks": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {"id": {"type": "integer"}, "asserts": K.assert_schema()},
            "required": ["id", "asserts"], "additionalProperties": False}}}, "required": ["tasks"], "additionalProperties": False}

GUIDE = """You are CODEBOT's planner. Turn the SPECIFICATION into a plan of small tasks, in the order of the spec (one task per numbered item, split an item only if it is clearly two things).
Nobody can answer questions. You only plan; you never write code.

For every task give: title (one concrete sentence), files (the repo-relative files it creates or changes), size (S, M or L), deps (numbers of earlier tasks it needs, usually empty),
and ASSERTIONS: the acceptance test, written as data. A trusted runner evaluates them in the code directory after the task is implemented. Assertion types:
  {"type":"call","module":"shapes","func":"area","args":[3,4],"expect":12}                                  call a function and compare the return value
  {"type":"call","module":"shapes","func":"area","args":[[]],"expect":0.0}
  {"type":"call","module":"shapes","func":"area","args":[-1,2],"raises":"ValueError"}                          the call must raise this exception class
  {"type":"run","argv":["python3","greet.py","Ada"],"stdout":"Hello, Ada!"}                                   run a command: stdout (exact), stdout_contains (list), stdout_json, exit (default 0)
  {"type":"file_contains","path":"settings.py","text":"DEBUG = False"}   {"type":"file_not_contains","path":"app.py","text":"legacy_mode"}   {"type":"file_exists","path":"greet.py"}
Assertions describe the FINISHED state: what must be true once the task is done. They fail today only because the work has not been done yet. So never assert that something the task creates is absent
(no file_not_contains for a function you are asked to add), and never assert two things that cannot both hold. file_not_contains is only for things the task REMOVES (for example the old name in a rename).
Use `call` to test a function (give its module, name, args and the expected return value or exception). Use `run` ONLY for a program the spec says is a command-line tool (for example `python3 greet.py Ada`);
never invent a command-line interface for a library function. Use 3 to 8 assertions per task covering the examples in the spec and the edge cases it names, and nothing the spec does not say: test only inputs whose result the spec states (never invent an edge case it does not define), use `raises` only when the spec says an error is raised (never to mean 'does not work yet'), and use file_contains only for text the spec names for that exact file (prefer testing behaviour with call or run over looking at source text). Use plain file names
relative to the code directory, never an absolute path. Also give: goal (one sentence), context (stack, conventions, off-limits),
done (how the whole thing is verified)."""

GUIDE_ITEM = GUIDE.replace("Turn the SPECIFICATION into a plan of small tasks, in the order of the spec (one task per numbered item, split an item only if it is clearly two things).",
                           "You plan ONE task at a time: the task for one numbered item of the SPECIFICATION, which the harness names. Plan only that item.").replace(
                           "For every task give:", "For the task give:").replace(
                           " Also give: goal (one sentence), context (stack, conventions, off-limits),\ndone (how the whole thing is verified).", "")

def _chat(proj, system, user, schema, left, num_predict=5000):
    r = ollama.chat(proj, [{"role": "system", "content": system}, {"role": "user", "content": user}], schema=schema, temperature=0.2, num_predict=num_predict, timeout=max(60, int(left)), retries=2,
                    model=roles.model(proj, "planner"), think=roles.think(proj, "planner"), role="planner")
    raw = r.get("content") or ""
    try: return json.loads(raw), None, raw
    except ValueError: return None, ("the reply was not valid JSON (cut off by the token limit?)" if r.get("done_reason") == "length" else "the reply was not valid JSON"), raw

spec_items = specdoc.count_items           # number of numbered items ('1. ...' or '2) ...') at the start of a line
item_texts = specdoc.item_texts            # the text of each numbered item, in order

def _frame(spec):
    """goal / context / done for PLAN.md, taken from the spec itself (no model call): the first heading, the text before the first numbered item."""
    head = next((l.lstrip("# ").strip() for l in spec.splitlines() if l.strip()), "Implement SPEC.md")
    m = re.search(r"^\s*\d+[.)]\s", spec, re.M); pre = spec[:m.start()] if m else spec
    ctx = " ".join(l.strip() for l in pre.splitlines() if l.strip() and not l.lstrip().startswith("#"))[:600]
    return {"goal": head, "context": ctx or "See SPEC.md.", "done": "every task's assertions hold"}

def ungrounded(asserts, spec):
    """Source-text assertions (file_contains / file_not_contains) must quote text the specification itself contains; anything else is the model's own
    invention (`def double` where the spec only says 'rename to double'). Returns (kept assertions, dropped assertions)."""
    flat = " ".join(spec.split()); kept, dropped = [], []
    for a in asserts if isinstance(asserts, list) else []:
        bad = isinstance(a, dict) and a.get("type") in ("file_contains", "file_not_contains") and isinstance(a.get("text"), str) and " ".join(a["text"].split()) not in flat
        (dropped if bad else kept).append(a)
    return kept, dropped

def salvage(asserts):
    """Keep the assertions that are usable on their own; returns (kept, [error of each dropped one]). A task loses only its bad assertions, not itself."""
    if not isinstance(asserts, list): return asserts, []
    kept, dropped = [], []
    for a in asserts:
        e = A.validate([a])
        (dropped.append(e) if e else kept.append(a))
    return kept, dropped

def _write(proj, plan, spec="", actor="planner"):
    """Repair, salvage, screen, validate and write every task's spec, then PLAN.md. With a spec, a task's assertions start with the spec's own worked
    examples for the requirement it implements (grounding.examples: written by the harness, sound by construction), and model assertions that cannot
    hold for a program following the spec are dropped (grounding.screen). Returns {task number: error} for tasks with no usable assertion left (no
    spec is written for them). Every repair, added example and dropped assertion is logged (plan.log op assertions_salvaged)."""
    bad, tasks = {}, []
    K.ensure_runner(proj)                                  # the trusted runner must be in checks/ before the guard runs the checks (freeze would copy it anyway)
    for f in os.listdir(K.C.checks_dir(proj)):             # specs of an earlier, longer version of this plan (a salvaged plan is renumbered)
        if re.fullmatch(r"T\d+\.spec\.json", f) and int(f[1:-len(".spec.json")]) > len(plan["tasks"]): os.remove(os.path.join(K.C.checks_dir(proj), f))
    groups = [K.prepare(t.get("asserts")) for t in plan["tasks"]]
    groups, notes = K.repair_plan(groups)
    staged = []
    for i, t in enumerate(plan["tasks"], 1):
        asserts = groups[i - 1]
        if spec and isinstance(asserts, list): asserts, _ = ungrounded(asserts, spec)         # over-specification is dropped, not argued about: the model repeated it in every repair round
        asserts, dropped = salvage(asserts)
        if dropped: notes += [f"T{i}: dropped an assertion: {e}" for e in dropped]
        staged.append(asserts)
    why = {}
    if spec:          # 2026-10-17: only 3 of the 41 checks the calc_go control rerun froze could pass for a correct program
        per_item = G.by_item(spec)
        prepend = [per_item[t["item"] - 1] if isinstance(t.get("item"), int) and 1 <= t["item"] <= len(per_item) else [] for t in plan["tasks"]]
        staged, screened, why = G.screen(staged, spec, G.project_kind(spec, proj.rundir), prepend, G.all_examples(spec))
        notes += screened
        if any(prepend): notes.append(f"added {sum(len(p) for p in prepend)} assertion(s) from the specification's own examples to {sum(1 for p in prepend if p)} task(s)")
    for i, t in enumerate(plan["tasks"], 1):
        asserts = staged[i - 1]
        err = A.validate(asserts)
        if err: bad[i] = err + (f" (every assertion was dropped: {'; '.join(dict.fromkeys(why[i - 1]))})"[:600] if why.get(i - 1) else "")
        else: K.write_spec(proj, f"T{i}", asserts)
        deps = [d for d in (t.get("deps") or []) if isinstance(d, int) and 1 <= d < i]
        tasks.append({"id": i, "title": t.get("title") or f"task {i}", "files": [f for f in (t.get("files") or []) if isinstance(f, str) and f.strip()], "size": t.get("size") or "S", "deps": deps})
    with open(proj.p("PLAN.md"), "w") as f: f.write(K.render_plan(plan.get("goal") or "", plan.get("context") or "", plan.get("done") or "", tasks))
    specdoc.save_map(proj, {f"T{i}": t["item"] for i, t in enumerate(plan["tasks"], 1) if isinstance(t.get("item"), int)})
    if notes: append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "harness", "op": "assertions_salvaged", "ok": True, "reason": f"{len(notes)} repair(s)/drop(s) by {actor}", "notes": notes[:40]})
    return bad

def drop_tasks(plan, numbers):
    """The plan without the given task numbers (1-based); later tasks are renumbered and deps remapped (a dep on a dropped task is removed)."""
    keep = [i for i in range(1, len(plan["tasks"]) + 1) if i not in set(numbers)]; new = {old: n for n, old in enumerate(keep, 1)}
    tasks = [dict(plan["tasks"][old - 1], deps=[new[d] for d in (plan["tasks"][old - 1].get("deps") or []) if isinstance(d, int) and d in new]) for old in keep]
    return {**plan, "tasks": tasks}

def _per_item(proj, spec, repo, sizing, left, num_predict, log):
    """One small model call per numbered item: each reply can only be that item's task, so the plan cannot collapse, merge items or pad itself with
    filler (all three seen on 2026-10-16). An item whose reply is unusable twice is left out (logged), not the whole plan. Returns (plan, [errors], {n: raw})."""
    items = item_texts(spec); tasks, errors, raws, t0 = [], [], {}, time.time()
    head = f"SPECIFICATION\n{spec}\n\nCODE DIRECTORY (read only, current files):\n{repo or '(empty)'}\n\n{('SIZING HISTORY: ' + sizing) if sizing else ''}\n"
    for n, text in enumerate(items, 1):
        task, err = None, "no time left in the planning budget"
        for attempt in (1, 2):
            budget = left - (time.time() - t0)
            if budget < 60: break
            user = head + (f"Plan exactly ONE task: the task for item {n} of {len(items)}:\n{text}\n\nIts assertions test only what item {n} says. deps: numbers of EARLIER "
                           f"items it needs (usually none). Reply with the JSON task.")
            try: task, err, raw = _chat(proj, GUIDE_ITEM, user, task_schema(), budget, min(num_predict, 2500))
            except ollama.ModelError as e: task, err, raw = None, f"model error: {e}", None
            if raw is not None: raws[f"{n}.{attempt}"] = raw
            if task is not None: break
        if task is None and G.examples(spec, text):          # the spec's own examples are a check without the model: plan the item from them (salvage)
            task = {"title": re.sub(r"^\s*\d+[.)]\s*", "", text).split(". ")[0].strip()[:120], "files": [], "size": "S", "deps": [], "asserts": []}
            log(f"planner item {n}: no usable reply ({err}); planned from the specification's own examples")
            append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "harness", "op": "item_from_examples", "ok": True, "reason": f"item {n}: {err}"})
        if task is None: errors.append(f"item {n}: {err}"); log(f"planner item {n}: no usable reply ({err}); left out"); continue
        task["deps"] = [d for d in (task.get("deps") or []) if isinstance(d, int) and 1 <= d < n]
        task["item"] = n; tasks.append(task)                                   # the requirement it implements: the worker is shown that part of the spec
    nums = {t["item"]: i for i, t in enumerate(tasks, 1)}                      # deps are item numbers; a left-out item shifts the task numbers
    for t in tasks: t["deps"] = [nums[d] for d in t["deps"] if d in nums]
    return ({**_frame(spec), "tasks": tasks} if tasks else None), errors, raws

def _evaluate(proj, bad):
    with open(proj.p("PLAN.md")) as f: _, tasks = planparse.parse_plan(f.read())
    rejected = []
    for r in guard.evaluate(proj, [t for t in tasks if int(t["id"][1:]) not in bad]):
        if r["verdict"] != "ok": rejected.append(r)
    for n, err in bad.items():
        t = next((x for x in tasks if x["id"] == f"T{n}"), {"title": ""})
        rejected.append({"id": f"T{n}", "title": t["title"], "check": "", "verdict": "malformed", "rc": 2, "out": err})
    return sorted(rejected, key=lambda r: int(r["id"][1:]))

def _why(r):
    if r["verdict"] == "passes": return "ALL assertions already hold on the current code, so they test nothing. Assert what is DIFFERENT after the work: call or run the new behaviour or new name, and for anything the task removes or renames use file_not_contains with the OLD text (never file_contains for it)"
    return "the assertions are unusable: " + (r["out"] or "unknown error").strip().replace("\n", " ")[:300]

def route_repairs(replies, ids):
    """Which rejected task each repair reply is for: the task it names, when that task was rejected; a lone reply to a lone rejection is for that
    task whatever number it gives. Returns ([(task number, reply)], [replies for no rejected task]). 2026-10-18 calc4: the planner answered T7's
    rejection with "id": 1 twice, and both replies replaced T1's assertions (the second time with the guide's own `shapes.area` example)."""
    if len(ids) == 1 and len(replies) == 1: return [(ids[0], replies[0])], []
    routed, stray = [], []
    for t in replies: (routed.append((t["id"], t)) if type(t.get("id")) is int and t["id"] in ids else stray.append(t))
    return routed, stray

def _sizing(home, name):
    try: return subprocess.run([sys.executable, os.path.join(home, "bin", "codebot-engine"), "sizing", name, "--home", home], capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError): return ""

def plan_auto(home, name, timeout=2700, rounds=3, log=print, max_tasks=10, num_predict=5000):
    """Same return shape as the opencode planner loop: {"rounds": [{n, secs, rc, rejected, violations, ok}], "ok", "secs"}."""
    proj = Project(name, home); t0 = time.time(); info = {"rounds": [], "ok": False, "secs": 0}
    try:
        with open(proj.p("SPEC.md")) as f: spec = f.read()
    except OSError: log("no SPEC.md in the project state directory"); return info
    repo, _ = packer.repo_map(proj.rundir, 2500); sizing = _sizing(home, name); plan, note = None, ""
    per_item = str(proj.get("PLAN_PER_ITEM", "1")).lower() not in ("0", "", "false", "no") and spec_items(spec) >= 2; last = []
    for k in range(1, rounds + 1):
        left = timeout - (time.time() - t0)
        if left < 60: break
        r0 = time.time(); rejected = []; rc = 0; why_failed = ""; raw = None
        try:
            if plan is None and per_item:
                plan, errs, raws = _per_item(proj, spec, repo, sizing, left, num_predict, log)
                for key, txt in raws.items():
                    with open(proj.p(f"plan.reply.{k}.item{key}.json"), "w") as f: f.write(txt)
                if errs: why_failed = "; ".join(errs)
                if plan is None: rc = 1
            elif plan is None:
                user = f"SPECIFICATION\n{spec}\n\nCODE DIRECTORY (read only, current files):\n{repo or '(empty)'}\n\n{('SIZING HISTORY: ' + sizing) if sizing else ''}\n{note}\nReply with the JSON plan."
                want = spec_items(spec)
                plan, err, raw = _chat(proj, GUIDE, user, plan_schema(max_tasks, want), left, num_predict)
                if plan is not None and plan.get("tasks") and len(plan["tasks"]) < want:
                    err, plan = f"it had {len(plan['tasks'])} task(s) but the specification has {want} numbered items; plan one task per item", None
                if plan is None or not plan.get("tasks"): why_failed = err or "it had no tasks"; plan, note, rc = None, f"Your previous reply was rejected: {err or 'it had no tasks'}. Reply with the complete JSON plan.", 1
            else:
                lines = [f"T{r['id'][1:]} \"{r['title'][:70]}\": {_why(r)}\n  current assertions: {json.dumps((K.read_spec(proj, r['id']) or plan['tasks'][int(r['id'][1:]) - 1].get('asserts')))[:700]}" for r in last]
                ids = [int(r["id"][1:]) for r in last]
                user = ("The harness ran your assertions against the code exactly as it is today and REJECTED these tasks:\n" + "\n".join(lines) +
                        f"\nReturn corrected assertions for ONLY these tasks, each with the task's number as \"id\" ({', '.join(map(str, ids))}). They must FAIL now and hold once the task is done.")
                fix, err, raw = _chat(proj, GUIDE, user, repair_schema(), left, 3000)
                if fix is None: rc = 1; why_failed = "repair reply: " + (err or "unusable")
                else:
                    routed, stray = route_repairs(fix["tasks"], ids)
                    for n, t in routed: plan["tasks"][n - 1]["asserts"] = t.get("asserts")
                    if stray: append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "harness", "op": "repair_ignored", "ok": True,
                                                                "reason": f"repair replies for tasks that were not rejected: {[t.get('id') for t in stray]} (rejected: {ids})"})
        except ollama.ModelError as e: log(f"planner round {k}: model error: {e}"); rc = 1; note = ""; why_failed = f"model error: {e}"
        if plan is not None and rc == 0:
            bad = _write(proj, plan, spec); rejected = _evaluate(proj, bad)
            for r in rejected: guard.log_rejection(proj, r["id"], r["title"], r["check"], r["verdict"], r["out"], "planner")
        ok = plan is not None and rc == 0 and not rejected
        if raw is not None:                                       # the model's own words, so a failed round can be read afterwards
            with open(proj.p(f"plan.reply.{k}.json"), "w") as f: f.write(raw)
        if rejected and not why_failed: why_failed = "; ".join(f"{r['id']} {r['verdict']}: {(r['out'] or '').strip()[:160]}" for r in rejected)
        info["rounds"].append({"n": k, "secs": round(time.time() - r0), "rc": rc, "rejected": [f"{r['id']}:{r['verdict']}" for r in rejected], "violations": [], "ok": ok,
                               "tasks": len(plan["tasks"]) if plan else None, **({"error": why_failed} if why_failed else {})})
        if why_failed and not ok: append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "planner", "op": "plan_round_failed", "ok": False, "round": k, "reason": why_failed[:600]})
        log(f"planner round {k}: {len(rejected)} rejected, 0 violations, ok={ok}" + (f": {why_failed[:300]}" if why_failed else ""))
        if ok: info["ok"] = True; break
        last = rejected
    if not info["ok"] and plan is not None and last and len(last) < len(plan["tasks"]):
        # the repair rounds are spent: keep the tasks whose checks work instead of failing the whole plan (2026-10-16: 2 bad tasks of 8 ended a trial at 0/64)
        gone = sorted(int(r["id"][1:]) for r in last); titles = [plan["tasks"][n - 1].get("title", "") for n in gone]
        plan = drop_tasks(plan, gone); bad = _write(proj, plan, spec); rejected = _evaluate(proj, bad)
        if not rejected:
            info["ok"] = True; info["dropped_tasks"] = titles
            append_jsonl(proj.p("plan.log"), {"ts": now(), "actor": "harness", "op": "plan_salvaged", "ok": True, "reason": f"dropped {len(gone)} task(s) whose checks stayed unusable: {titles}"})
            log(f"planner: kept {len(plan['tasks'])} task(s), dropped {len(gone)} whose checks stayed unusable: {titles}")
    info["secs"] = round(time.time() - t0); return info

def install_plan(proj, plan):
    """Write a ready-made plan (same shape as the model's reply: goal, context, done, tasks[{title, files, size, deps, asserts}]) exactly as the planner would:
    spec files, PLAN.md, then the baseline guard. Used for the reference-plan diagnostic arm. Returns the rejected tasks (empty = every check is a real, working failing test)."""
    bad = _write(proj, plan); return _evaluate(proj, bad)

def audit_against_reference(home, name, suite):
    """Planner quality, measured: run every generated spec against the seed code (a real test FAILS there: rc 1) and against the suite's reference solution
    (a sound test HOLDS there: rc 0). Returns {"tasks": {id: {"seed": rc, "reference": rc, "why": [...]}}, "unsound": [ids], "vacuous": [ids]} or None without a reference."""
    import shutil, tempfile
    src = os.path.join(home, "bench", "suite", suite); ref = os.path.join(src, "reference"); seed = os.path.join(src, "seed")
    if not (os.path.isdir(ref) and os.path.isdir(seed)): return None
    proj = Project(name, home); out = {"tasks": {}, "unsound": [], "vacuous": []}; runner = os.path.join(proj.p("checks"), K.RUNNER)
    tmp = tempfile.mkdtemp(prefix="cb-audit-")
    try:
        for label in ("seed", "reference"):
            d = os.path.join(tmp, label); shutil.copytree(seed, d)
            if label == "reference": shutil.copytree(ref, d, dirs_exist_ok=True)
        for fn in sorted(os.listdir(proj.p("checks"))):
            if not fn.endswith(".spec.json"): continue
            tid = fn[:-len(".spec.json")]; row = {}
            for label in ("seed", "reference"):
                r = subprocess.run([sys.executable, runner, os.path.join(proj.p("checks"), fn)], cwd=os.path.join(tmp, label), capture_output=True, text=True, timeout=300)
                row[label] = r.returncode
                if label == "reference" and r.returncode: row["why"] = [l[6:150] for l in r.stdout.splitlines() if l.startswith("FAIL: ")][:3]
            out["tasks"][tid] = row
            if row["reference"] != 0: out["unsound"].append(tid)            # a correct solution fails it: the model's own check could never be satisfied
            if row["seed"] == 0: out["vacuous"].append(tid)                  # already holds on the seed: tests nothing
    finally: shutil.rmtree(tmp, ignore_errors=True)
    return out
