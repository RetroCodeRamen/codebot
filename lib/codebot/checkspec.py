"""Harness side of declarative checks (see assertions.py). The model emits ASSERTIONS as JSON constrained by a schema; this module turns them into
files the frozen-check machinery already understands: checks/<id>.spec.json plus a check line that runs the trusted runner. The model never sees a
path, an interpreter or a shell, so a whole family of planner mistakes (wrong paths, backticks, <placeholders>, wrong interpreter) cannot happen."""
import json, os, shutil
from . import assertions as A, checks as C

RUNNER = "assert_runner.py"

def ensure_runner(proj):
    """Put the trusted runner in checks/ (freeze would copy it from the mode's verifiers anyway; a bare home or an older project may not have it yet)."""
    dst = os.path.join(C.checks_dir(proj), RUNNER)
    if not os.path.isfile(dst):
        os.makedirs(C.checks_dir(proj), exist_ok=True); shutil.copyfile(A.__file__, dst)
    return dst

def command_for(tid): return f"python3 $CODEBOT_CHECKS/{RUNNER} $CODEBOT_CHECKS/{tid}.spec.json"
def is_runner_check(check): return RUNNER in (check or "")
def spec_name(tid): return f"{tid}.spec.json"

def assert_schema():
    """JSON schema for the list of assertions. One strict variant per way an assertion can be valid, so constrained decoding cannot produce a field the
    type does not take, a call with nothing to compare, or a run with no expectation (the mistakes a flat all-optional schema allowed)."""
    s, j = {"type": "string"}, {}                       # j: any JSON value
    P = {"module": s, "func": s, "args": {"type": "array", "items": j}, "kwargs": {"type": "object"}, "expect": j, "raises": s, "argv": {"type": "array", "items": s, "minItems": 1},
         "stdin": s, "exit": {"type": "integer"}, "stdout": s, "stdout_contains": {"type": "array", "items": s, "minItems": 1}, "stdout_json": j, "path": s, "text": s}
    def variant(t, required, optional=()):
        keys = ["type", *required, *optional]
        return {"type": "object", "properties": {"type": {"enum": [t]}, **{k: P[k] for k in keys if k != "type"}}, "required": ["type", *required], "additionalProperties": False}
    variants = [variant("call", ("module", "func", "args", "expect"), ("kwargs",)), variant("call", ("module", "func", "args", "raises"), ("kwargs",))]     # args is REQUIRED (use [] for none): a call without its inputs tests nothing real
    run_exp = ("exit", "stdout", "stdout_contains", "stdout_json")
    variants += [variant("run", ("argv", e), ("stdin", *[x for x in run_exp if x != e])) for e in run_exp]
    variants += [variant("file_exists", ("path",)), variant("file_contains", ("path", "text")), variant("file_not_contains", ("path", "text"))]
    return {"type": "array", "minItems": 1, "maxItems": 12, "items": {"anyOf": variants}}

def clean(a):
    """Keep only the fields the type takes. With the strict per-type schema the model cannot emit filler any more, so values are kept AS GIVEN: an empty
    string is a real expectation ("prints nothing", "returns an empty string"). Only a `raises` assertion drops a stray `expect`."""
    if not isinstance(a, dict) or a.get("type") not in A.TYPES: return a
    req, opt = A.TYPES[a["type"]]
    out = {k: v for k, v in a.items() if k == "type" or k in req | opt}
    if out.get("raises"): out.pop("expect", None)
    # ornith writes a schema field name into the inputs when the expected value is empty ("args": ["", "expect"], "expect": ""): every real run so far,
    # and repair rounds repeat it. The intended check is plain (slugify("") == ""), so the stray word is removed instead of failing the plan.
    if isinstance(out.get("args"), list): out["args"] = [x for x in out["args"] if not (isinstance(x, str) and x in A.LEAKED)]
    if isinstance(out.get("argv"), list) and len(out["argv"]) > 1: out["argv"] = out["argv"][:1] + [x for x in out["argv"][1:] if not (isinstance(x, str) and x in A.LEAKED)]
    return out

def prepare(asserts): return [clean(a) for a in asserts or []]

def _arities(groups):
    """{func: the most common number of positional args} over every call in a plan (or a split plus its parent)."""
    from collections import Counter
    c = {}
    for asserts in groups:
        for a in asserts or []:
            if isinstance(a, dict) and a.get("type") == "call" and isinstance(a.get("args"), list): c.setdefault(a.get("func"), Counter())[len(a["args"])] += 1
    return {f: n.most_common(1)[0][0] for f, n in c.items()}

def _same_value(x, want): return x is want if isinstance(want, bool) or isinstance(x, bool) else x == want

def repair(a, arity):
    """Deterministic repairs of call assertions whose intent is unambiguous, judged on the replay corpus (tests/test_w_corpus.py: 8 of 8 impossible
    assertions became sound, 0 sound ones changed). arity: this function's usual number of positional args in the same plan. Returns (assertion, note)."""
    if not isinstance(a, dict) or a.get("type") != "call" or not isinstance(a.get("args"), list) or arity is None: return a, None
    args = a["args"]
    want = a["raises"] if "raises" in a else a.get("expect", object())
    if len(args) == arity + 1 and _same_value(args[-1], want):         # the expected value copied in as an extra last input: slugify("", "") == ""
        return {**a, "args": args[:-1]}, f"{a.get('func')}: removed an extra last input equal to the expected value"
    if arity == 1 and len(args) > 1 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in args):
        return {**a, "args": [list(args)]}, f"{a.get('func')}: numbers spread over several inputs were put back into one list"     # mean(1, 2, 3) -> mean([1, 2, 3])
    return a, None

def repair_plan(groups, context=()):
    """Repair every assertion list in groups using the arities seen across groups + context (assertion lists that are not repaired, e.g. a split's
    parent). Returns (repaired groups, [notes])."""
    ar = _arities(list(groups) + list(context)); out, notes = [], []
    for asserts in groups:
        fixed = []
        for a in asserts or []:
            b, note = repair(a, ar.get(a.get("func")) if isinstance(a, dict) else None)
            fixed.append(b)
            if note: notes.append(note)
        out.append(fixed if isinstance(asserts, list) else asserts)
    return out, notes

def write_spec(proj, tid, asserts):
    """Write checks/<id>.spec.json (a fixture: it is hashed and locked at freeze) and return the check command. Overwrites only before freezing."""
    os.makedirs(C.checks_dir(proj), exist_ok=True)
    with open(os.path.join(C.checks_dir(proj), spec_name(tid)), "w") as f: json.dump({"asserts": asserts}, f, indent=1)
    return command_for(tid)

def read_spec(proj, tid):
    try:
        with open(os.path.join(C.checks_dir(proj), spec_name(tid))) as f: return json.load(f).get("asserts")
    except (OSError, ValueError): return None

def render_plan(goal, context, done, tasks):
    """PLAN.md text in the standard format. tasks: [{id, title, files, size, deps}] (ids are 1..n)."""
    L = ["# Goal", goal.strip(), "# Context", context.strip(), "# Definition of done", done.strip(), "# Tasks"]
    for t in tasks:
        L.append(f"- [ ] {t['id']}. {t['title'].strip()}")
        L.append(f"  - check: `{command_for('T' + str(t['id']))}`")
        if t.get("files"): L.append(f"  - files: {', '.join(t['files'])}")
        L.append(f"  - size: {t.get('size') or 'S'}")
        if t.get("deps"): L.append(f"  - deps: {', '.join(str(d) for d in t['deps'])}")
    return "\n".join(L) + "\n"
