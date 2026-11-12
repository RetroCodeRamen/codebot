"""Mechanical gates: the harness runs the check itself, on a scratch copy of the workspace. No model's word passes a gate.

  examples       the frozen spec's worked examples (and its build command) are run against the code in the workspace, as drafts mode judges a version
  tests          the verify.sh that existed when the spec was frozen is run in the sandbox; exit status 0 passes
  qa             the QA member's own tests (qa_tests.py, which only that member may write) are run in the sandbox; exit status 0 passes. They are
                 the QA agent's reading of the spec, so a workflow ends in the examples gate as well and may let a QA gate run out of retries
  spec-examples  the spec has at least one example codebot can run (a gate for a spec-writing workflow, before any code exists); passing freezes it
The spec and verify.sh a result is judged by are the frozen copies (frozen.py), which no agent can write.
  approve        the user's approval (PIPELINE_AUTO_APPROVE=0), else passes
"""
import copy, json, os, re, shutil
from codebot import assertions as A, checkspec as K, drafts, sandbox
from . import frozen, runtime as R
from .validate import GATE_CHECKS as CHECKS

IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".gocache")

def _spec(proj, ws):
    """The spec code is judged by: frozen now if it was not yet (frozen.py)."""
    frozen.freeze(proj, ws); frozen.restore(proj, ws)
    return frozen.text(proj, ws)

def _scratch(proj, ws):
    """The project as drafts.evaluate sees it, with its state under pipeline/gate (a drafts bridge's own state is not touched) and the workspace
    as it is now for a baseline."""
    q = copy.copy(proj); q.state = os.path.join(R.pipeline_dir(proj), "gate")
    base = drafts.dpath(q, "baseline"); shutil.rmtree(base, ignore_errors=True)
    shutil.copytree(ws, base, symlinks=True, ignore=IGNORE)
    return q

def _file_of(a):
    """The source file an example exercises: the module of a call, the script of `python3 tool.py ...`; None when it cannot be told."""
    if a["type"] == "call": return a["module"] + ".py" if a.get("module") else None
    av = a.get("argv") or []
    return av[1] if a["type"] == "run" and len(av) > 1 and av[0] in ("python", "python3") and av[1].endswith(".py") else None

def examples(proj, ws, only=None):
    j = judge(proj, ws, only)
    return j["when"], j["detail"]

def judge(proj, ws, only=None):
    """{when, detail, passed, total, failing}: failing = the 1-based numbers of the examples that fail (None when the runner could not say; every
    example when the code does not build), so two judgements of the same frozen spec can be compared example by example.
    only = the spec's files one worker writes: then just the examples that call into those files are run (and just those files must exist), so
    a worker is not told its part fails because another worker's file is not written yet. Only for specs where every example names its file: a
    function call into a module, or a `python3 tool.py ...` command."""
    res = lambda when, detail, passed=0, total=0, failing=None: {"when": when, "detail": detail, "passed": passed, "total": total, "failing": failing}
    spec = _spec(proj, ws)
    if not spec.strip(): return res("fail", "there is no SPEC.md in the workspace")
    build, checks = drafts.examples(spec)
    if not checks: return res("fail", "SPEC.md has no example codebot can run, so there is nothing to judge the code by (codebot check-spec SPEC.md)")
    names = drafts.spec_files(spec); decl = drafts.declared(spec, names); part = ""
    if only is not None and not build and all(_file_of(a) for a in checks):
        names = [n for n in names if n in only]; decl = {k: v for k, v in decl.items() if v in only}
        checks = [a for a in checks if _file_of(a) in only]; part = f" for your files ({', '.join(only)})"
        if not checks: return res("pass", f"no example in SPEC.md calls into your files ({', '.join(only)}), so there is nothing to run")
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
    head = f"{passed} of {len(checks)} spec examples{part} pass" + (f", {defined} of {len(decl)} declared names defined" if decl else "")
    m = re.search(r"^failing assertions: ([\d, ]+)$", feedback or "", re.M)
    failing = [] if passed == len(checks) else list(range(1, len(checks) + 1)) if not built else [int(x) for x in m.group(1).split(",")] if m else None
    return res("pass" if ok else "fail", head if ok else f"{head}\n{feedback}".strip(), passed, len(checks), failing)

def tests(proj, ws):
    frozen.freeze(proj, ws); frozen.restore(proj, ws); script = frozen.verify_script(proj)
    if frozen.info(proj) is None: script = frozen._read(os.path.join(ws, "verify.sh"))              # no spec at all: nothing was frozen
    if script is None: return "fail", "there was no verify.sh when the specification was frozen (one written during the build is not run)"
    q = _scratch(proj, ws); wsc = drafts.dpath(q, "ws"); drafts._reset_ws(wsc, drafts.dpath(q, "baseline"))
    with open(os.path.join(wsc, "verify.sh"), "w", encoding="utf-8") as f: f.write(script)
    timeout = proj.get("PIPELINE_GATE_TIMEOUT", 600, int)
    rc, out, _ = sandbox.execute(drafts._at(q, wsc), ["bash", "verify.sh"], timeout=timeout, rw=[wsc], cwd=wsc)
    if rc == 0: return "pass", "verify.sh exited with status 0"
    return "fail", f"verify.sh exited with status {rc}:\n{out.strip()[-2000:]}"

QA_FILE = "qa_tests.py"

def qa(proj, ws):
    frozen.freeze(proj, ws); frozen.restore(proj, ws)
    if not os.path.isfile(os.path.join(ws, QA_FILE)): return "fail", f"QA wrote no tests ({QA_FILE} does not exist)"
    q = _scratch(proj, ws); wsc = drafts.dpath(q, "ws"); drafts._reset_ws(wsc, drafts.dpath(q, "baseline"))
    rc, out, _ = sandbox.execute(drafts._at(q, wsc), ["python3", QA_FILE], timeout=proj.get("PIPELINE_GATE_TIMEOUT", 600, int), rw=[wsc], cwd=wsc)
    if rc == 0: return "pass", f"{QA_FILE} exited with status 0"
    return "fail", f"QA's tests failed ({QA_FILE} exited with status {rc}):\n{out.strip()[-2500:]}"

def spec_examples(proj, ws):
    spec = frozen.text(proj, ws)
    if not spec.strip(): return "fail", "there is no SPEC.md in the workspace"
    n = drafts.coverage(spec).get("examples", 0)
    if not n: return "fail", "SPEC.md has no example codebot can run (codebot check-spec SPEC.md)"
    cur = frozen.freeze(proj, ws)
    return "pass", f"SPEC.md has {n} examples codebot can run; frozen as version {cur['version']}"

def run(proj, bundle, node):
    """(\"pass\" | \"fail\", what was run and what it showed). A check that is unknown or cannot be run fails."""
    check = node.get("check") or "approve"; ws = R.workspace_of(proj, bundle)
    if check == "approve":
        return ("pass", "approved automatically (PIPELINE_AUTO_APPROVE)") if proj.get("PIPELINE_AUTO_APPROVE", "1") != "0" else ("fail", "not approved")
    fn = {"examples": examples, "tests": tests, "spec-examples": spec_examples, "qa": qa}.get(check)
    if not fn: return "fail", f"unknown check {check!r} (known: {', '.join(CHECKS)})"
    try: return fn(proj, ws)
    except Exception as e: return "fail", f"the {check} check could not be run: {type(e).__name__}: {e}"
