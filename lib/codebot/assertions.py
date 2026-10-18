"""Declarative acceptance checks. The planner/splitter model never writes a shell command, an interpreter, or a path: it emits a list of
ASSERTIONS (data) and the harness runs them with this one trusted runner (copied into every project's frozen checks/ as assert_runner.py).

    python3 $CODEBOT_CHECKS/assert_runner.py $CODEBOT_CHECKS/T1.spec.json      (run in the code directory)

Exit status is a contract the baseline guard relies on:
    0  every assertion holds                         (a check that holds today tests nothing)
    1  at least one assertion does NOT hold          (the only status that means "a real test that fails now")
    2  the spec itself is unusable                   (bad JSON, unknown type, missing field, unsafe path: a planner mistake, never a failing test)
Standard library only; this file is also imported by the harness to validate specs before anything runs."""
import importlib, json, math, os, subprocess, sys

PY = ("python", "python3")

# type -> (required fields, optional fields); the JSON schema offered to the model is derived from this table (checkspec.assert_schema)
TYPES = {
    "call": ({"module", "func"}, {"args", "kwargs", "expect", "raises"}),
    "run": ({"argv"}, {"stdin", "exit", "stdout", "stdout_contains", "stdout_json"}),
    "file_exists": ({"path"}, set()),
    "file_contains": ({"path", "text"}, set()),
    "file_not_contains": ({"path", "text"}, set()),
}

# Field names a 9B planner leaked into the DATA (real runs: "args": ["", "expect"] whenever the expected value was "", argv ending in "stdout_contains").
# No real input is exactly one of these words, and a check carrying one can never pass, so it is a spec error rather than a failing test.
LEAKED = {"expect", "raises", "args", "kwargs", "argv", "stdin", "stdout", "stdout_contains", "stdout_json"}

def _leaked(values):
    return next((v for v in values if isinstance(v, str) and v in LEAKED), None)

def _python_c(argv):
    """The code of `python3 -c CODE ...`, or None."""
    if argv[0] not in PY: return None
    i = next((k for k, x in enumerate(argv[1:], 1) if x == "-c"), None)
    return argv[i + 1] if i is not None and i + 1 < len(argv) else None

def _safe_rel(p):
    return isinstance(p, str) and p and not os.path.isabs(p) and ".." not in p.replace("\\", "/").split("/") and "\x00" not in p

def validate(asserts):
    """Return an error message or None. Used by the harness (before writing a spec) and by the runner (exit 2)."""
    if not isinstance(asserts, list) or not asserts: return "asserts must be a non-empty list"
    for i, a in enumerate(asserts, 1):
        if not isinstance(a, dict) or a.get("type") not in TYPES: return f"assertion {i}: type must be one of {', '.join(TYPES)}"
        req, opt = TYPES[a["type"]]; keys = set(a) - {"type"}
        if req - keys: return f"assertion {i} ({a['type']}): missing {', '.join(sorted(req - keys))}"
        if keys - req - opt: return f"assertion {i} ({a['type']}): unknown field {', '.join(sorted(keys - req - opt))}"
        t = a["type"]
        if t == "call":
            if not (isinstance(a["module"], str) and a["module"].replace("_", "").isalnum() and not a["module"][0].isdigit()): return f"assertion {i}: module must be a plain module name like textutil"
            if not (isinstance(a["func"], str) and a["func"].replace("_", "").isalnum()): return f"assertion {i}: func must be a plain function name"
            if not isinstance(a.get("args", []), list) or not isinstance(a.get("kwargs", {}), dict): return f"assertion {i}: args is a list and kwargs an object"
            if "expect" not in a and "raises" not in a: return f"assertion {i}: call needs expect (the return value) or raises (an exception class name)"
            if "expect" in a and "raises" in a: return f"assertion {i}: give expect OR raises, not both"
            w = _leaked([*a.get("args", []), *a.get("kwargs", {}), *a.get("kwargs", {}).values()])
            if w: return f"assertion {i}: the field name {w!r} is inside the inputs; args holds only the values passed to {a['func']}, and the expected result goes in expect"
        elif t == "run":
            av = a["argv"]
            if not (isinstance(av, list) and av and all(isinstance(x, str) for x in av)): return f"assertion {i}: argv is a list of strings, e.g. [\"python3\", \"cli.py\", \"slug\", \"Hello World\"]"
            if not (set(a) & {"exit", "stdout", "stdout_contains", "stdout_json"}): return f"assertion {i}: run needs at least one of exit, stdout, stdout_contains, stdout_json"
            if "exit" in a and not isinstance(a["exit"], int): return f"assertion {i}: exit must be an integer"
            w = _leaked(av[1:])
            if w: return f"assertion {i}: the field name {w!r} is inside argv; argv holds only the command and its arguments"
            code = _python_c(av)
            if code is not None:
                try: compile(code, "<python3 -c>", "exec")
                except (SyntaxError, ValueError) as e: return f"assertion {i}: the python3 -c code is not valid Python ({type(e).__name__}: {getattr(e, 'msg', e)}), so it can never pass"
        else:
            if not _safe_rel(a["path"]): return f"assertion {i}: path must be a relative path inside the code directory"
            if t != "file_exists" and not (isinstance(a["text"], str) and a["text"]): return f"assertion {i}: text must be a non-empty string"
    seen = {(a["type"], a["path"], a["text"]) for a in asserts if a["type"] in ("file_contains", "file_not_contains")}
    for a in asserts:
        if a["type"] == "file_not_contains" and ("file_contains", a["path"], a["text"]) in seen:
            return f"contradiction: {a['path']} is asserted both to contain and not to contain {_short(a['text'], 40)}; assertions describe the finished state"
    return None

def _same(got, want):
    if isinstance(got, bool) or isinstance(want, bool): return got is want
    if isinstance(got, (int, float)) and isinstance(want, (int, float)): return math.isclose(got, want, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(got, (list, tuple)) and isinstance(want, list): return len(got) == len(want) and all(_same(g, w) for g, w in zip(got, want))
    if isinstance(got, dict) and isinstance(want, dict): return set(got) == set(want) and all(_same(got[k], want[k]) for k in want)
    return got == want

def _short(v, n=120):
    s = repr(v); return s if len(s) <= n else s[:n] + "..."

def _call(a):
    try: mod = importlib.import_module(a["module"])
    except BaseException as e: return f"cannot import module {a['module']}: {type(e).__name__}: {str(e)[:120]}"
    fn = getattr(mod, a["func"], None)
    if not callable(fn): return f"{a['module']}.{a['func']} does not exist"
    label = f"{a['func']}({', '.join([_short(x, 40) for x in a.get('args', [])] + [f'{k}={_short(v, 40)}' for k, v in a.get('kwargs', {}).items()])})"
    try: got = fn(*a.get("args", []), **a.get("kwargs", {}))
    except BaseException as e:
        if "raises" in a: return None if type(e).__name__ == a["raises"] or any(c.__name__ == a["raises"] for c in type(e).__mro__) else f"{label} raised {type(e).__name__}, expected {a['raises']}"
        return f"{label} raised {type(e).__name__}: {str(e)[:100]}, expected {_short(a.get('expect'))}"
    if "raises" in a: return f"{label} returned {_short(got)} instead of raising {a['raises']}"
    return None if _same(got, a["expect"]) else f"{label} returned {_short(got)}, expected {_short(a['expect'])}"

def _run(a):
    argv = [sys.executable if a["argv"][0] in PY else a["argv"][0], *a["argv"][1:]]
    try: p = subprocess.run(argv, input=a.get("stdin"), capture_output=True, text=True, timeout=60)
    except FileNotFoundError:                     # a path (./calc) is a program the task has not built yet: a real failing test; a bare name is a tool
        c = a["argv"][0]                          # the sandbox does not have, which can never pass (the guard rejects it as broken)
        return f"{c} does not exist yet" if "/" in c else f"command not available in the sandbox: {c}"
    except subprocess.TimeoutExpired: return f"{' '.join(a['argv'])} timed out after 60 s"
    cmd = " ".join(a["argv"]); out = p.stdout.strip(); want_exit = a.get("exit", 0)
    if p.returncode != want_exit: return f"{cmd} exited {p.returncode}, expected {want_exit}" + (f" (stderr: {p.stderr.strip()[-150:]})" if p.stderr.strip() else "")
    if "stdout" in a and out != a["stdout"].strip(): return f"{cmd} printed {_short(out)}, expected {_short(a['stdout'].strip())}"
    for s in ([a["stdout_contains"]] if isinstance(a.get("stdout_contains"), str) else a.get("stdout_contains", [])):
        if s not in p.stdout: return f"{cmd} output does not contain {_short(s)} (got {_short(out)})"
    if "stdout_json" in a:
        try: got = json.loads(out)
        except ValueError: return f"{cmd} did not print JSON (got {_short(out)})"
        if not _same(got, a["stdout_json"]): return f"{cmd} printed JSON {_short(got)}, expected {_short(a['stdout_json'])}"
    return None

def _file(a):
    path = a["path"]
    if not os.path.isfile(path): return f"file {path} does not exist"
    if a["type"] == "file_exists": return None
    try:
        with open(path, errors="replace") as f: body = f.read()
    except OSError as e: return f"cannot read {path}: {e}"
    has = a["text"] in body
    if a["type"] == "file_contains": return None if has else f"{path} does not contain {_short(a['text'])}"
    return f"{path} still contains {_short(a['text'])}" if has else None

def failures(asserts):
    """Run every assertion; returns [(its 1-based number, failure message)] (empty = all hold)."""
    sys.path.insert(0, os.getcwd()); fails = []
    for n, a in enumerate(asserts, 1):
        msg = _call(a) if a["type"] == "call" else _run(a) if a["type"] == "run" else _file(a)
        if msg: fails.append((n, msg))
    return fails

def check(asserts):
    """Run every assertion; returns the list of failure messages (empty = all hold)."""
    return [m for _, m in failures(asserts)]

def main(argv):
    if len(argv) != 2: print("SPEC ERROR: usage: assert_runner.py <spec.json>"); return 2
    try:
        with open(argv[1]) as f: spec = json.load(f)
    except (OSError, ValueError) as e: print(f"SPEC ERROR: cannot read {argv[1]}: {e}"); return 2
    asserts = spec.get("asserts") if isinstance(spec, dict) else None
    err = validate(asserts)
    if err: print(f"SPEC ERROR: {err}"); return 2
    fails = failures(asserts)
    for _, m in fails: print("FAIL: " + m)
    if fails: print("failing assertions: " + ", ".join(str(n) for n, _ in fails))      # the numbers the worker's context shows; the harness tracks them per attempt
    print(f"{len(fails)} of {len(asserts)} assertions failed" if fails else f"all {len(asserts)} assertions hold")
    return 1 if fails else 0

if __name__ == "__main__": sys.exit(main(sys.argv))
