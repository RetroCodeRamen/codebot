"""Native unattended planner. ONE constrained-output model call turns SPEC.md into a structured plan (tasks, files, sizes, deps and declarative
ASSERTIONS); the harness writes everything else: the spec files, the check commands, PLAN.md. Then the harness runs the baseline guard itself and, for
rejected tasks only, asks the model for corrected assertions with the exact reason (repair rounds). The model never writes a path, a shell command, an
interpreter or a file, so the mistakes the free-form opencode planner made (wrong paths, backticks, <placeholders>, scripts run with the wrong
interpreter, implementation written instead of tests) cannot occur. The interactive `codebot plan` (a human in the loop) is unchanged."""
import json, os, re, subprocess, sys, time
from . import assertions as A, checkspec as K, guard, ollama, packer, planparse
from .config import Project

TASK_PROPS = lambda: {"title": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}, "size": {"enum": ["S", "M", "L"]},
                      "deps": {"type": "array", "items": {"type": "integer"}}, "asserts": K.assert_schema()}

def plan_schema():
    s = {"type": "string"}
    return {"type": "object", "properties": {"goal": s, "context": s, "done": s, "tasks": {"type": "array", "minItems": 1, "maxItems": 10, "items": {
        "type": "object", "properties": TASK_PROPS(), "required": ["title", "files", "size", "asserts"], "additionalProperties": False}}},
        "required": ["goal", "context", "done", "tasks"], "additionalProperties": False}

def repair_schema():
    return {"type": "object", "properties": {"tasks": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {"id": {"type": "integer"}, "asserts": K.assert_schema()},
            "required": ["id", "asserts"], "additionalProperties": False}}}, "required": ["tasks"], "additionalProperties": False}

GUIDE = """You are CODEBOT's planner. Turn the SPECIFICATION into a plan of small tasks, in the order of the spec (one task per numbered item, split an item only if it is clearly two things).
Nobody can answer questions. You only plan; you never write code.

For every task give: title (one concrete sentence), files (the repo-relative files it creates or changes), size (S, M or L), deps (numbers of earlier tasks it needs, usually empty),
and ASSERTIONS: the acceptance test, written as data. A trusted runner evaluates them in the code directory after the task is implemented. Assertion types:
  {"type":"call","module":"textutil","func":"slugify","args":["Hello World"],"expect":"hello-world"}     call a function and compare the return value
  {"type":"call","module":"stats","func":"mean","args":[[]],"expect":0.0}
  {"type":"call","module":"calc","func":"evaluate","args":["1/0"],"raises":"ValueError"}                  the call must raise this exception class
  {"type":"run","argv":["python3","cli.py","slug","Hello World"],"stdout":"hello-world"}                   run a command: stdout (exact), stdout_contains (list), stdout_json, exit (default 0)
  {"type":"file_contains","path":"legacy.py","text":"def double"}   {"type":"file_not_contains","path":"main_app.py","text":"old_name"}   {"type":"file_exists","path":"cli.py"}
The assertions MUST FAIL on the code as it is today (the feature does not exist yet) and hold once the task is truly done. Use 3 to 8 assertions per task covering the examples in the
spec and the edge cases it names. Use plain file names relative to the code directory, never an absolute path. Also give: goal (one sentence), context (stack, conventions, off-limits),
done (how the whole thing is verified)."""

def _chat(proj, system, user, schema, left, num_predict=5000):
    r = ollama.chat(proj, [{"role": "system", "content": system}, {"role": "user", "content": user}], schema=schema, temperature=0.2, num_predict=num_predict, timeout=max(60, int(left)), retries=2)
    try: return json.loads(r["content"]), None
    except ValueError: return None, "the reply was not valid JSON (cut off by the token limit?)" if r.get("done_reason") == "length" else "the reply was not valid JSON"

def _write(proj, plan):
    """Validate and write every task's spec, then PLAN.md. Returns {task number: error} for tasks whose assertions are unusable (no spec is written for them)."""
    bad, tasks = {}, []
    K.ensure_runner(proj)                                  # the trusted runner must be in checks/ before the guard runs the checks (freeze would copy it anyway)
    for i, t in enumerate(plan["tasks"], 1):
        asserts = K.prepare(t.get("asserts")); err = A.validate(asserts)
        if err: bad[i] = err
        else: K.write_spec(proj, f"T{i}", asserts)
        deps = [d for d in (t.get("deps") or []) if isinstance(d, int) and 1 <= d < i]
        tasks.append({"id": i, "title": t.get("title") or f"task {i}", "files": [f for f in (t.get("files") or []) if isinstance(f, str) and f.strip()], "size": t.get("size") or "S", "deps": deps})
    with open(proj.p("PLAN.md"), "w") as f: f.write(K.render_plan(plan.get("goal") or "", plan.get("context") or "", plan.get("done") or "", tasks))
    return bad

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
    if r["verdict"] == "passes": return "ALL assertions already hold on the current code, so they test nothing; assert behaviour that does not exist yet"
    return "the assertions are unusable: " + (r["out"] or "unknown error").strip().replace("\n", " ")[:300]

def _sizing(home, name):
    try: return subprocess.run([sys.executable, os.path.join(home, "bin", "codebot-engine"), "sizing", name, "--home", home], capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError): return ""

def plan_auto(home, name, timeout=2700, rounds=3, log=print):
    """Same return shape as the opencode planner loop: {"rounds": [{n, secs, rc, rejected, violations, ok}], "ok", "secs"}."""
    proj = Project(name, home); t0 = time.time(); info = {"rounds": [], "ok": False, "secs": 0}
    try:
        with open(proj.p("SPEC.md")) as f: spec = f.read()
    except OSError: log("no SPEC.md in the project state directory"); return info
    repo, _ = packer.repo_map(proj.rundir, 2500); sizing = _sizing(home, name); plan, note = None, ""
    for k in range(1, rounds + 1):
        left = timeout - (time.time() - t0)
        if left < 60: break
        r0 = time.time(); rejected = []; rc = 0
        try:
            if plan is None:
                user = f"SPECIFICATION\n{spec}\n\nCODE DIRECTORY (read only, current files):\n{repo or '(empty)'}\n\n{('SIZING HISTORY: ' + sizing) if sizing else ''}\n{note}\nReply with the JSON plan."
                plan, err = _chat(proj, GUIDE, user, plan_schema(), left)
                if plan is None or not plan.get("tasks"): plan, note, rc = None, f"Your previous reply was rejected: {err or 'it had no tasks'}. Reply with the complete JSON plan.", 1
            else:
                lines = [f"T{r['id'][1:]} \"{r['title'][:70]}\": {_why(r)}\n  current assertions: {json.dumps((K.read_spec(proj, r['id']) or plan['tasks'][int(r['id'][1:]) - 1].get('asserts')))[:700]}" for r in last]
                user = "The harness ran your assertions against the code exactly as it is today and REJECTED these tasks:\n" + "\n".join(lines) + "\nReturn corrected assertions for ONLY these tasks (use their numbers). They must FAIL now and hold once the task is done."
                fix, err = _chat(proj, GUIDE, user, repair_schema(), left, 3000)
                if fix is None: rc = 1
                else:
                    for t in fix["tasks"]:
                        if isinstance(t.get("id"), int) and 1 <= t["id"] <= len(plan["tasks"]): plan["tasks"][t["id"] - 1]["asserts"] = t.get("asserts")
        except ollama.ModelError as e: log(f"planner round {k}: model error: {e}"); rc = 1; note = ""
        if plan is not None and rc == 0:
            bad = _write(proj, plan); rejected = _evaluate(proj, bad)
            for r in rejected: guard.log_rejection(proj, r["id"], r["title"], r["check"], r["verdict"], r["out"], "planner")
        ok = plan is not None and rc == 0 and not rejected
        info["rounds"].append({"n": k, "secs": round(time.time() - r0), "rc": rc, "rejected": [f"{r['id']}:{r['verdict']}" for r in rejected], "violations": [], "ok": ok})
        log(f"planner round {k}: {len(rejected)} rejected, 0 violations, ok={ok}")
        if ok: info["ok"] = True; break
        last = rejected
    info["secs"] = round(time.time() - t0); return info

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
