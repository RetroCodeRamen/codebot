"""Mechanical gates: the harness runs the check itself, on a scratch copy of the workspace. No model's word passes a gate.

  examples       the spec's worked examples (and its build command) are run against the code in the workspace, as drafts mode judges a version
  tests          `verify.sh` in the workspace is run in the sandbox; exit status 0 passes
  spec-examples  the spec has at least one example codebot can run (a gate for a spec-writing workflow, before any code exists)
  approve        the user's approval (PIPELINE_AUTO_APPROVE=0), else passes
"""
import copy, json, os, shutil
from codebot import assertions as A, checkspec as K, drafts, sandbox
from . import runtime as R
from .validate import GATE_CHECKS as CHECKS

IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".gocache")

def _spec(proj, ws):
    for p in (os.path.join(ws, "SPEC.md"), proj.p("SPEC.md")):
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f: return f.read()
    return ""

def _scratch(proj, ws):
    """The project as drafts.evaluate sees it, with its state under pipeline/gate (a drafts bridge's own state is not touched) and the workspace
    as it is now for a baseline."""
    q = copy.copy(proj); q.state = os.path.join(R.pipeline_dir(proj), "gate")
    base = drafts.dpath(q, "baseline"); shutil.rmtree(base, ignore_errors=True)
    shutil.copytree(ws, base, symlinks=True, ignore=IGNORE)
    return q

def examples(proj, ws):
    spec = _spec(proj, ws)
    if not spec.strip(): return "fail", "there is no SPEC.md in the workspace"
    build, checks = drafts.examples(spec)
    if not checks: return "fail", "SPEC.md has no example codebot can run, so there is nothing to judge the code by (codebot check-spec SPEC.md)"
    names = drafts.spec_files(spec); decl = drafts.declared(spec, names)
    q = _scratch(proj, ws); cdir = drafts.dpath(q, "checks"); os.makedirs(cdir, exist_ok=True)
    shutil.copyfile(A.__file__, os.path.join(cdir, K.RUNNER))
    with open(os.path.join(cdir, "checks.json"), "w") as f: json.dump({"asserts": checks}, f, indent=1)
    files = {}
    for n in names:
        if drafts.SAFE.match(n) and os.path.isfile(os.path.join(ws, n)):
            with open(os.path.join(ws, n), encoding="utf-8", errors="replace") as f: files[n] = f.read()
    st = {"names": names, "language": drafts.language(spec, names, sorted(os.listdir(ws))), "build": build, "declared": decl}
    built, passed, defined, feedback = drafts.evaluate(q, st, files)
    ok = built and passed == len(checks) and defined == len(decl)
    head = f"{passed} of {len(checks)} spec examples pass" + (f", {defined} of {len(decl)} declared names defined" if decl else "")
    return ("pass" if ok else "fail"), (head if ok else f"{head}\n{feedback}".strip())

def tests(proj, ws):
    if not os.path.isfile(os.path.join(ws, "verify.sh")): return "fail", "there is no verify.sh in the workspace"
    q = _scratch(proj, ws); wsc = drafts.dpath(q, "ws"); drafts._reset_ws(wsc, drafts.dpath(q, "baseline"))
    timeout = proj.get("PIPELINE_GATE_TIMEOUT", 600, int)
    rc, out, _ = sandbox.execute(drafts._at(q, wsc), ["bash", "verify.sh"], timeout=timeout, rw=[wsc], cwd=wsc)
    if rc == 0: return "pass", "verify.sh exited with status 0"
    return "fail", f"verify.sh exited with status {rc}:\n{out.strip()[-2000:]}"

def spec_examples(proj, ws):
    spec = _spec(proj, ws)
    if not spec.strip(): return "fail", "there is no SPEC.md in the workspace"
    n = drafts.coverage(spec).get("examples", 0)
    return ("pass", f"SPEC.md has {n} examples codebot can run") if n else ("fail", "SPEC.md has no example codebot can run (codebot check-spec SPEC.md)")

def run(proj, bundle, node):
    """(\"pass\" | \"fail\", what was run and what it showed). A check that is unknown or cannot be run fails."""
    check = node.get("check") or "approve"; ws = R.workspace_of(proj, bundle)
    if check == "approve":
        return ("pass", "approved automatically (PIPELINE_AUTO_APPROVE)") if proj.get("PIPELINE_AUTO_APPROVE", "1") != "0" else ("fail", "not approved")
    fn = {"examples": examples, "tests": tests, "spec-examples": spec_examples}.get(check)
    if not fn: return "fail", f"unknown check {check!r} (known: {', '.join(CHECKS)})"
    try: return fn(proj, ws)
    except Exception as e: return "fail", f"the {check} check could not be run: {type(e).__name__}: {e}"
