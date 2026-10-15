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
    """JSON schema for the list of assertions (one flat object with a type enum: the same shape the flat worker schema uses, which every server compiles)."""
    s, j = {"type": "string"}, {}                       # j: any JSON value
    props = {"type": {"enum": list(A.TYPES)}, "module": s, "func": s, "args": {"type": "array", "items": j}, "kwargs": {"type": "object"}, "expect": j, "raises": s,
             "argv": {"type": "array", "items": s}, "stdin": s, "exit": {"type": "integer"}, "stdout": s, "stdout_contains": {"type": "array", "items": s}, "stdout_json": j,
             "path": s, "text": s}
    return {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "object", "properties": props, "required": ["type"], "additionalProperties": False}}

def clean(a):
    """Drop the empty/placeholder fields a constrained model fills in for types that do not use them (flat schema), keeping only what the type allows."""
    if not isinstance(a, dict) or a.get("type") not in A.TYPES: return a
    req, opt = A.TYPES[a["type"]]
    out = {k: v for k, v in a.items() if k == "type" or (k in req | opt and v not in (None, "") or (k in ("expect", "stdout_json") and k in a and k in req | opt))}
    if out.get("raises"): out.pop("expect", None)       # a call expects a value OR an exception; the flat schema makes the model fill both
    if out.get("stdout_json") in ("", None): out.pop("stdout_json", None)
    return out

def prepare(asserts): return [clean(a) for a in asserts or []]

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
