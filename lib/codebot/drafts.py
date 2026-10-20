"""Drafts: the whole program from the spec, judged by the spec's own examples (2026-10-19).

The model gets SPEC.md and replies with every file whole (a line `### name` and one code block per file). The harness builds the code and runs the
spec's worked examples (grounding: assertions the harness derives from the spec, never the model) in the sandbox, and sends back what failed; a
Python file must also define every function and exception the spec declares (checked in its syntax tree, no code runs). A draft that has not
improved for DRAFTS_PATIENCE rounds is dropped and the next round starts a fresh draft from the spec alone. The version delivered is the best of all
drafts by that signal (it builds, then the most checks pass; the latest among equals) and is committed to the code dir each time it improves. No
planner, no tools, no model-written checks. Why: on calc_go, level_01 and level_03 this loop beat the planner/task pipeline in well under half the
time (docs/MODEL_EVAL.md, "A plain loop without codebot" and the paragraphs after it); bench/baselines/bare_py.py is the measured original and the
prompts here are the same text.

Three rules come from the 114 repair rounds of the 13 recorded loop runs (2026-10-19, docs/MODEL_EVAL.md "Tuning drafts mode"): every new best
version came from a fresh draft, from its first repair or from a repair right after one that improved, never after a repair that did not improve
(0 of 54), so DRAFTS_PATIENCE is 1; a repair reply may be at most RUNAWAY_FACTOR times the longest complete reply that built so far (at least
RUNAWAY_FLOOR tokens), because 10 replies ran to the 6000-token cap for 12.5 min each and none of them was useful; and the delivered level_03
module of one run lacked one of its six functions while every example passed, which the declared-name check catches.

One step = one round (one model call and one evaluation). The state lives in <state>/drafts/, so a pause, a crash or a reboot resumes at the round it
was in. What it cannot do: check a requirement the spec gives no example for (coverage() lists them before a run), and work on code too large to send
whole in every prompt (fits() says so before a run)."""
import ast, builtins, copy, hashlib, json, os, re, shutil, subprocess, sys, time
from . import assertions as A, audit, checkspec as K, gitops, grounding, ollama, sandbox, specdoc, thermal, tokens
from .config import append_jsonl, now

D = "drafts"
# language: (source extensions, code fence, fence words of an unnamed block, how a reply without fences starts, comment starts, the file a lone unnamed block is when the spec names none)
LANGS = {
    "Python": ((".py",), "python", ("", "python", "py", "python3"), ("import", "from", "def", "class", "#", '"""'), ("#",), "main.py"),
    "Go": ((".go",), "go", ("", "go", "golang"), ("package", "//", "import"), ("//",), "main.go"),
    "C": ((".c", ".h"), "c", ("", "c"), ("#include", "//", "/*"), ("//", "/*"), "main.c"),
    "C++": ((".cpp", ".cc", ".hpp", ".h"), "cpp", ("", "cpp", "c++"), ("#include", "//", "/*"), ("//", "/*"), "main.cpp"),
    "JavaScript": ((".js", ".mjs"), "javascript", ("", "javascript", "js"), ("//", "const ", "import ", "function ", "'use strict'", '"use strict"'), ("//",), "main.js"),
    "TypeScript": ((".ts",), "typescript", ("", "typescript", "ts"), ("//", "const ", "import ", "function "), ("//",), "main.ts"),
    "Rust": ((".rs",), "rust", ("", "rust", "rs"), ("use ", "fn ", "//"), ("//",), None),
    "Ruby": ((".rb",), "ruby", ("", "ruby", "rb"), ("#", "require", "def ", "class "), ("#",), "main.rb"),
    "Bash": ((".sh",), "bash", ("", "bash", "sh", "shell"), ("#!", "#", "set "), ("#",), None),
}
SOURCE_EXT = tuple(sorted({e for v in LANGS.values() for e in v[0]}))
CONFIG_FILES = ("go.mod", "go.sum", "Cargo.toml", "package.json", "tsconfig.json", "pyproject.toml", "requirements.txt", "setup.cfg", "Makefile", "CMakeLists.txt")
TOKEN = re.compile(r"(?<![\w.])([A-Za-z_][\w-]*\.(?:py|go|mod|sum|rs|c|h|cc|cpp|hpp|js|mjs|ts|rb|md|txt|json|cfg|ini|toml|ya?ml|sh))(?!\w)")   # a file name anywhere in a line
SAFE = re.compile(r"^[A-Za-z_][\w.-]*$")              # the model writes flat file names in the code dir, never a path, never a dot file
MAX_FILE, MAX_SHOWN = 20_000, 60_000                    # a baseline file larger than this is listed by name only; above MAX_SHOWN in all, drafts mode does not fit
MAX_TREE_FILES, MAX_TREE_BYTES = 2000, 50 * 1024 ** 2   # the code dir is copied as the baseline: drafts mode is for small projects
GO_BIN = "/usr/local/go/bin"
RUNAWAY_FACTOR, RUNAWAY_FLOOR = 3, 3000                  # the output cap of a reply once a complete reply has built (see the module docstring)
EXC = re.compile(r"`([A-Z]\w*(?:Error|Exception))`")

class DraftsError(Exception):
    pass

def is_drafts(proj): return (proj.cfg.get("WORKFLOW") or "") == D           # project.env only: an exported WORKFLOW must never switch other projects
def dpath(proj, *a): return proj.p(D, *a)

def settings(proj):
    g = proj.get
    return {"minutes": g("DRAFTS_MINUTES", 60.0, float), "patience": g("DRAFTS_PATIENCE", 1, int), "max_rounds": g("DRAFTS_MAX_ROUNDS", 60, int),
            "num_predict": g("DRAFTS_NUM_PREDICT", 6000, int), "temperature": g("DRAFTS_TEMPERATURE", 0.2, float), "seed": g("DRAFTS_SEED", 1, int)}

# ---------- what the spec asks for ----------
def spec_files(spec):
    """The source files the spec names (a whole `name.py` span), plus the script of a `python3 tool.py ...` example: the files a version must have."""
    out = {m for m in re.findall(r"`([A-Za-z_][\w-]*\.[a-z]+)`", spec) if m.endswith(SOURCE_EXT)}
    for a in grounding.all_examples(spec) if specdoc.item_texts(spec) else grounding.examples(spec, spec):
        av = a.get("argv") or []
        if a["type"] == "run" and len(av) > 1 and av[0] in grounding.PY and SAFE.match(av[1]) and av[1].endswith(".py"): out.add(av[1])
    return sorted(out)

def language(spec, names, baseline):
    """The main language: of the files the spec names, else of the build command it gives, else of the code already there, else Python."""
    def of(fn): return next((k for k, v in LANGS.items() if fn.endswith(v[0])), None)
    for group in (names, [n for n in baseline if n.endswith(SOURCE_EXT)]):
        langs = [of(n) for n in group if of(n)]
        if langs: return max(dict.fromkeys(langs), key=langs.count)
    build, _ = grounding.build_step(spec)
    if build: return {"go": "Go", "g++": "C++", "clang++": "C++"}.get(build[0], "C")
    if "go.mod" in baseline: return "Go"
    return "Python"

def examples(spec):
    """(build argv or None, the example assertions without the build step). A spec without numbered requirements is read as one requirement."""
    build, _ = grounding.build_step(spec)
    ex = grounding.all_examples(spec) if specdoc.item_texts(spec) else grounding.examples(spec, spec)
    step = {"type": "run", "argv": build, "exit": 0} if build else None
    return build, K.prepare([a for a in ex if a != step])

def declared(spec, names):
    """{name: file} a Python version must define at the top level of a file: every function the spec declares with a signature and a module
    ("`textutil.py` provides `slugify(text)`", or the only module it names), and, when it names one module, the exception classes it mentions
    (`MissingDependencyError`). Python's builtins never count (`str(error)` in prose is not a declaration)."""
    if not names or not all(n.endswith(".py") for n in names): return {}
    mods, out = grounding.modules(spec), {}
    for fn in grounding._declared(spec):
        if fn in mods and not hasattr(builtins, fn) and f"{mods[fn]}.py" in names: out[fn] = f"{mods[fn]}.py"
    if len(names) == 1:
        for e in EXC.findall(spec):
            if not hasattr(builtins, e): out[e] = names[0]
    return dict(sorted(out.items()))

def defined(path):
    """The names a Python file binds at its top level (also inside top-level if / try / with / for blocks), or None when it does not parse."""
    try:
        with open(path, encoding="utf-8") as f: tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError): return None
    out, todo = set(), list(tree.body)
    while todo:
        n = todo.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): out.add(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]): out |= {x.id for x in ast.walk(t) if isinstance(x, ast.Name)}
        elif isinstance(n, (ast.Import, ast.ImportFrom)): out |= {(a.asname or a.name).split(".")[0] for a in n.names}
        else:
            for f in ("body", "orelse", "finalbody"):
                if isinstance(getattr(n, f, None), list): todo += getattr(n, f)
            for h in getattr(n, "handlers", None) or []: todo += h.body
    return out

def coverage(spec):
    """Which requirements have worked examples: nothing checks the others, except that a declared name exists.
    {requirements: [{n, title, examples, names}], uncovered, examples, declared, build, files, language}"""
    items = specdoc.item_texts(spec); build, ex = examples(spec)
    step = {"type": "run", "argv": build, "exit": 0} if build else None
    names = spec_files(spec); decl = declared(spec, names); reqs = []
    for i, it in enumerate(items or [spec], 1):
        first = re.sub(r"^\s*\d+[.)]\s*", "", it.strip().splitlines()[0]) if it.strip() else ""
        reqs.append({"n": i, "title": first[:70] + ("..." if len(first) > 70 else ""), "examples": len([a for a in grounding.examples(spec, it) if a != step]),
                     "names": [n for n in decl if n in grounding._declared(it) or n in EXC.findall(it)]})
    return {"numbered": bool(items), "requirements": reqs, "uncovered": [r["n"] for r in reqs if not r["examples"]], "examples": len(ex), "declared": list(decl),
            "build": " ".join(build) if build else None, "files": names, "language": language(spec, names, [])}

def coverage_text(cov, path="SPEC.md"):
    n = len(cov["requirements"])
    nd = len(cov.get("declared") or [])
    L = [f"{path}: {n} requirement{'s' * (n != 1)}, {cov['examples']} example{'s' * (cov['examples'] != 1)} codebot can run"
         + (f", {nd} declared name{'s' * (nd != 1)} it checks exist" if nd else "")
         + (f", built with `{cov['build']}`" if cov["build"] else "") + (f"; files: {', '.join(cov['files'])}" if cov["files"] else f"; language: {cov['language']}") + "."]
    if not cov["numbered"]: L.append("(no numbered requirements: the whole text is read as one)")
    for r in cov["requirements"]:
        names = f" (only that {', '.join(r.get('names') or [])} exist{'s' * (len(r.get('names') or []) == 1)})" if not r["examples"] and r.get("names") else ""
        L.append(f"  {r['n']:>2}. {r['title']:<74} {r['examples'] or 'NO'} example{'s' * (r['examples'] != 1)}{names}")
    if not cov["examples"]: L.append("No examples at all: a run can only check that the code builds, and it stops at the first version that does.")
    elif cov["uncovered"]: L.append(f"Nothing will check requirement{'s' * (len(cov['uncovered']) != 1)} {', '.join(map(str, cov['uncovered']))}: they have no examples. Add some, e.g.")
    else: L.append("Every requirement has at least one example. Only the examples are checked: a rule no example shows is still unchecked.")
    if cov["examples"] == 0 or cov["uncovered"]:
        L.append('  `slugify("Hello World")` is `"hello-world"`      `parse("")` raises `ValueError`      `calc 2 + 3` prints `5`')
        L.append("  (a call of a function the spec declares with its module, `is`/`returns`/`raises`; a command line with `prints`)")
    return "\n".join(L)

# ---------- the code directory ----------
def _listing(root):
    """({name: text} of the files a prompt shows, [names listed only]) for the top level of a code dir; sub-directories are listed by name."""
    shown, named = {}, []
    for n in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        p = os.path.join(root, n)
        if n.startswith(".") or n == "__pycache__": continue
        if os.path.isdir(p): named.append(n + "/"); continue
        if not (n.endswith(SOURCE_EXT) or n in CONFIG_FILES): named.append(n); continue
        try:
            with open(p, encoding="utf-8") as f: text = f.read(MAX_FILE + 1)
        except (OSError, UnicodeDecodeError): named.append(n); continue
        if "\0" in text or len(text) > MAX_FILE: named.append(n)
        else: shown[n] = text
    return shown, named

def _tree_size(root):
    """(files, bytes) under root without .git, stopping early once either limit is passed."""
    n = b = 0
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d != ".git"]
        for f in fn:
            n += 1
            try: b += os.lstat(os.path.join(dp, f)).st_size
            except OSError: pass
        if n > MAX_TREE_FILES or b > MAX_TREE_BYTES: break
    return n, b

def fits(proj_or_none, spec, code_dir):
    """None when spec + code leave room for a whole-file reply in the context window, else the reason (drafts mode sends every file in every prompt)."""
    if code_dir and os.path.isdir(code_dir):
        n, b = _tree_size(code_dir)
        if n > MAX_TREE_FILES or b > MAX_TREE_BYTES: return f"the code dir holds more than {MAX_TREE_FILES} files or {MAX_TREE_BYTES // 1024 ** 2} MB; drafts mode is for small projects (use the task pipeline: codebot plan)"
    shown, _ = _listing(code_dir) if code_dir else ({}, [])
    size = len(spec) + sum(len(t) for t in shown.values())
    if size > MAX_SHOWN: return f"the spec and the code are {size // 1000} KB; drafts mode sends them whole in every prompt and works up to {MAX_SHOWN // 1000} KB"
    if proj_or_none is not None:
        ctx, out = tokens.num_ctx(proj_or_none), settings(proj_or_none)["num_predict"]
        need = tokens.est(proj_or_none, spec) * 2 + tokens.est(proj_or_none, "".join(shown.values())) * 2 + out
        if need > ctx: return f"spec + code + a whole reply need about {need} tokens; the context window is {ctx}"
    return None

def _fence(n):
    return next((v[1] for v in LANGS.values() if n.endswith(v[0])), "")

def show(files):
    return "\n\n".join(f"### {n}\n```{_fence(n)}\n{files[n].rstrip()}\n```" for n in sorted(files))

def _join(names): return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

def system_prompt(lang):
    return (f"You are an expert {lang} programmer. You write complete, correct, working code. Reply with the complete contents of every file, "
            f"each as a line `### <file name>` followed by one ```{LANGS[lang][1]} code block, and nothing else.")

def first_prompt(spec, names, shown, named):
    if shown: where = f"The code directory contains {_join(named) + ' and ' if named else ''}these files:\n\n{show(shown)}"
    elif named: where = f"The code directory contains only {_join(named)}."
    else: where = "The code directory is empty."
    return (f"Write the code this specification describes.\n\n{where}\n\nSpecification (SPEC.md):\n\n{spec.strip()}\n\n"
            f"Reply with the complete contents of every file{' (' + ', '.join(names) + ')' if names else ''}.")

def repair_prompt(spec, files, named, feedback):
    lead = _join(named) + " and " if named else ""
    have = f"these files:\n\n{show(files)}" if files else "no code yet."
    return (f"Specification (SPEC.md):\n\n{spec.strip()}\n\nThe code directory contains {lead}{have}\n\n{feedback}\n\n"
            "Fix the code so that it meets the whole specification. Reply with the complete corrected contents of every file.")

def extract(reply, names, lang):
    """{file name: code} from a reply. names: the files the spec names (only these are taken), or [] (any file of the language is taken, and a lone
    unnamed block is the language's default file). parts[1], parts[3], ... are the insides of code blocks; with an odd number of fences the last block
    was cut off and is kept as it is. A block belongs to the one file named on its fence line, else on the last line before it, else in the text since
    the previous block, else on a comment first line. A block with none of those is the only file when there is one (the spec's, or the default),
    unless the line before it names another file (the model also writes README.md) or its fence names another language. The last block for a file
    wins (the model often drafts, then rewrites). Ported from bench/baselines/bare_py.py, whose first version read `### fileutil.py` as `util.py`."""
    exts, _, words, starts, comments, default = LANGS[lang]
    ok = (lambda t: t in names) if names else (lambda t: t.endswith(exts) and bool(SAFE.match(t)))
    only = names[0] if len(names) == 1 else (default if not names else None)
    parts, out = reply.split("```"), {}
    if len(parts) == 1 and only and reply.lstrip().startswith(starts): return {only: reply}
    def named(text):
        found = {t for t in TOKEN.findall(text) if ok(t)}
        return found.pop() if len(found) == 1 else None
    for i in range(1, len(parts), 2):
        info, _, code = parts[i].partition("\n")
        lines = [l for l in parts[i - 1].split("\n") if l.strip()]; head = lines[-1] if lines else ""; first = code.lstrip().split("\n", 1)[0]
        name = named(info) or named(head) or named(parts[i - 1]) or (named(first) if first.startswith(comments) else None)
        if not name and only and not TOKEN.search(head) and (info.split() or [""])[0].lower() in words: name = only
        if name: out[name] = code
    return out

# ---------- judging one version ----------
def label(a):
    return f"{a['func']}({', '.join(repr(x) for x in a.get('args', []))})"[:120] if a["type"] == "call" else " ".join(a.get("argv", []))[:120]

def _at(proj, ws):
    """The project as the sandbox sees it when the code dir is the scratch copy ws ({rundir} in SANDBOX_SETENV, the writable mount, the cwd)."""
    q = copy.copy(proj); q.rundir = ws; return q

def _timed_out(rc, out): return rc == 124 and "[timed out after" in out

def _env(st, ws):
    """Go needs its toolchain on the sandbox's PATH and a build cache that survives between rounds (a project's SANDBOX_PATH / SANDBOX_SETENV still win)."""
    if st["language"] != "Go" and (st.get("build") or [""])[0] != "go": return {}
    e = {"GOCACHE": os.path.join(ws, ".gocache"), "GOTOOLCHAIN": "local", "GOFLAGS": "-mod=mod"}
    if os.path.isdir(GO_BIN): e["PATH"] = GO_BIN + ":" + sandbox.BASE_ENV["PATH"]
    return e

def run_examples(proj, ws, checks, env=None):
    """(failed, runner output). The runner makes the calls in its own process with no time limit, so a run that hangs (a call that never returns, an
    is_prime that counts to n) is redone one example at a time, 20 s each."""
    cdir = dpath(proj, "checks"); spec = os.path.join(cdir, "checks.json")
    runner = [sys.executable if sandbox.mode(proj)["mode"] == "none" else "python3", os.path.join(cdir, K.RUNNER)]
    rc, out, _ = sandbox.execute(_at(proj, ws), [*runner, spec], timeout=120, rw=[ws], ro=[cdir], cwd=ws, env=env)
    if not _timed_out(rc, out):
        if rc == 0: return 0, out.strip()
        m = re.search(r"(\d+) of \d+ assertions failed", out)
        return (int(m.group(1)) if m else len(checks)), out.strip()
    fails, one = [], os.path.join(cdir, "one.json")
    for a in checks:
        with open(one, "w") as f: json.dump({"asserts": [a]}, f)
        rc, o, _ = sandbox.execute(_at(proj, ws), [*runner, one], timeout=20, rw=[ws], ro=[cdir], cwd=ws, env=env)
        if _timed_out(rc, o): fails.append(f"FAIL: {label(a)} did not finish within 20 seconds"); continue
        if rc: fails += [l for l in o.splitlines() if l.startswith("FAIL: ")] or [f"FAIL: {label(a)}"]
    return len(fails), "\n".join(fails) + f"\n{len(fails)} of {len(checks)} assertions failed"

def _reset_ws(ws, base):
    """A fresh copy of the baseline; hidden directories at the top (build caches such as .gocache) survive between rounds."""
    os.makedirs(ws, exist_ok=True)
    for e in os.listdir(ws):
        p = os.path.join(ws, e)
        if e.startswith(".") and os.path.isdir(p) and not os.path.islink(p): continue
        shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
    shutil.copytree(base, ws, dirs_exist_ok=True, symlinks=True)

def evaluate(proj, st, files):
    """(built, examples passed, declared names defined, feedback) for one version, judged in a fresh copy of the baseline: missing files, then
    compiling (Python is compiled and its names read on the host: that runs none of the model's code), then the spec's build command and its examples
    in the sandbox. With every declared name defined the feedback is bench/baselines/bare_py.py's text."""
    ws = dpath(proj, "ws"); _reset_ws(ws, dpath(proj, "baseline"))
    for n, code in files.items():
        with open(os.path.join(ws, n), "w") as f: f.write(code)
    missing = [n for n in st["names"] if n not in files and not os.path.exists(os.path.join(ws, n))]
    if missing: return False, 0, 0, f"These files are missing: {', '.join(missing)}."
    errs = [p.stderr.strip()[-1500:] for p in (subprocess.run([sys.executable, "-m", "py_compile", n], cwd=ws, capture_output=True, text=True, timeout=60)
                                                for n in sorted(files) if n.endswith(".py")) if p.returncode]
    if errs: return False, 0, 0, "The code does not compile:\n```\n" + "\n\n".join(errs)[-3000:] + "\n```"
    decl = st.get("declared") or {}; have = {f: defined(os.path.join(ws, f)) or set() for f in set(decl.values())}
    gone = [n for n, f in decl.items() if n not in have[f]]
    lack = ("these names the specification declares are not defined: " + ", ".join(f"`{n}` ({decl[n]})" for n in gone) + ".") if gone else ""
    env = _env(st, ws)
    if st.get("build"):
        rc, out, _ = sandbox.execute(_at(proj, ws), st["build"], timeout=300, rw=[ws], cwd=ws, env=env)
        if rc: return False, 0, 0, f"`{' '.join(st['build'])}` failed:\n```\n{out.strip()[-2000:]}\n```"
    checks = _checks(proj); total = len(checks); failed, out = run_examples(proj, ws, checks, env) if total else (0, "")
    names = len(decl) - len(gone)
    if not failed:
        if not gone: return True, total, names, ""
        return True, total, names, f"All files compile and {'all ' + str(total) + ' examples from the specification pass' if total else 'there is no example to run'}, but {lack}"
    out = out if len(out) <= 6000 else out[:6000] + "\n... (more failures cut)"
    return True, total - failed, names, f"All files compile, but {failed} of {total} examples from the specification fail:\n{out}" + (f"\n\nAlso, {lack}" if gone else "")

# ---------- state ----------
def _checks(proj):
    with open(dpath(proj, "checks", "checks.json")) as f: return json.load(f)["asserts"]

def load(proj):
    try:
        with open(dpath(proj, "state.json")) as f: return json.load(f)
    except (OSError, ValueError): return None

def _save(proj, st):
    tmp = dpath(proj, "state.json.tmp")
    with open(tmp, "w") as f: json.dump(st, f, indent=1)
    os.replace(tmp, dpath(proj, "state.json"))

def init(proj):
    """First step: freeze the spec, its examples and the code dir as they are now (every fresh draft starts from this baseline)."""
    spec = specdoc.load(proj)
    if not spec.strip(): raise DraftsError(f"no SPEC.md in {proj.state}")
    if not (proj.rundir and os.path.isdir(proj.rundir)): raise DraftsError(f"the code dir {proj.rundir!r} does not exist")
    why = fits(proj, spec, proj.rundir)
    if why: raise DraftsError(why)
    os.makedirs(dpath(proj, "checks"), exist_ok=True); base = dpath(proj, "baseline"); shutil.rmtree(base, ignore_errors=True)
    shutil.copytree(proj.rundir, base, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__", ".gocache"))
    with open(dpath(proj, "SPEC.md"), "w") as f: f.write(spec)
    build, checks = examples(spec); shutil.copyfile(A.__file__, dpath(proj, "checks", K.RUNNER))
    with open(dpath(proj, "checks", "checks.json"), "w") as f: json.dump({"asserts": checks}, f, indent=1)
    names = spec_files(spec); shown, named = _listing(base); decl = declared(spec, names)
    st = {"version": 2, "created": now(), "round": 0, "draft": 0, "fresh": True, "stale": 0, "dbest": None, "best": None, "used_s": 0.0,
          "names": names, "language": language(spec, names, list(shown) + named), "build": build, "examples_total": len(checks),
          "declared": decl, "declared_total": len(decl), "max_complete": 0,
          "uncovered": coverage(spec)["uncovered"], "delivered": [], "finished": None, "model_errors": 0, "settings": settings(proj)}
    _save(proj, st); audit.log(proj, "drafts_init", examples=len(checks), names=names, language=st["language"], build=build)
    return st

def deliver(proj, st, files, rnd, draft, passed, names=0):
    """Make the code dir hold this version (the baseline plus its files) and commit it. A file an earlier best version added and this one lacks goes
    back to its baseline state (deleted if the baseline did not have it)."""
    code, base = proj.rundir, dpath(proj, "baseline")
    for n in st["delivered"]:
        if n in files or not SAFE.match(n): continue
        if os.path.isfile(os.path.join(base, n)): shutil.copyfile(os.path.join(base, n), os.path.join(code, n))
        elif os.path.isfile(os.path.join(code, n)): os.remove(os.path.join(code, n))
    for n, text in files.items():
        if not SAFE.match(n): continue
        with open(os.path.join(code, n), "w") as f: f.write(text)
    st["delivered"] = sorted(files); total = st["examples_total"]; dt = st.get("declared_total", 0)
    msg = f"drafts: round {rnd} (draft {draft}) builds, {passed} of {total} spec examples pass" + (f", {names} of {dt} declared names defined" if dt else "")
    sha = gitops.commit_all(code, msg) if gitops.is_repo(code) else None
    audit.log(proj, "drafts_deliver", round=rnd, draft=draft, examples=passed, total=total, declared=names, commit=sha, files=sorted(files))
    return sha

def _spec(proj):
    with open(dpath(proj, "SPEC.md")) as f: return f.read()

def output_cap(st, cfg):
    """DRAFTS_NUM_PREDICT, or less once a complete reply has built: RUNAWAY_FACTOR times the longest such reply, at least RUNAWAY_FLOOR."""
    m = st.get("max_complete") or 0
    return min(cfg["num_predict"], max(RUNAWAY_FLOOR, RUNAWAY_FACTOR * m)) if m else cfg["num_predict"]

def _model_call(proj, st, cfg, rnd, user):
    tw = thermal.wait(proj); cap = output_cap(st, cfg)
    msgs = [{"role": "system", "content": system_prompt(st["language"])}, {"role": "user", "content": user}]
    r = ollama.chat(proj, msgs, temperature=cfg["temperature"], num_predict=cap, timeout=3600, options={"seed": cfg["seed"] * 1000 + rnd},
                    think=False, role="drafts")
    return {**r, "thermal_wait": tw, "num_predict": cap}

def step(proj):
    """One round. Returns the event: round | all_done | finished_incomplete | model_error | error."""
    try: st = load(proj) or init(proj)
    except DraftsError as e: return {"event": "error", "error": str(e)}
    if st["finished"]: return final_event(st)
    cfg = st["settings"] = settings(proj); spec = _spec(proj); base_shown, named = _listing(dpath(proj, "baseline"))     # settings are read every round: a longer DRAFTS_MINUTES applies at once
    if st["used_s"] > cfg["minutes"] * 60: return finish(proj, st, "time cap")
    if st["round"] >= cfg["max_rounds"]: return finish(proj, st, "round cap")
    t0 = time.time(); rnd = st["round"] + 1
    if st["fresh"]: draft, stale, dbest = st["draft"] + 1, 0, None; user = first_prompt(spec, st["names"], base_shown, named)
    else: draft, stale, dbest = st["draft"], st["stale"], st["dbest"]; user = repair_prompt(spec, {**base_shown, **dbest["files"]}, named, dbest["feedback"])
    try: r = _model_call(proj, st, cfg, rnd, user)
    except ollama.ModelError as e:
        st["model_errors"] += 1; st["used_s"] += time.time() - t0; _save(proj, st)
        if st["model_errors"] >= 5: return finish(proj, st, f"model errors ({e})")
        return {"event": "model_error", "error": str(e)[:300], "n": st["model_errors"]}
    d = dpath(proj, "rounds", f"{rnd:02d}"); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "reply.md"), "w") as f: f.write(r["content"])
    with open(os.path.join(d, "prompt.md"), "w") as f: f.write(user)
    got = extract(r["content"], st["names"], st["language"]); base = dbest["files"] if dbest else {}
    rec = {"round": rnd, "draft": draft, "fresh": st["fresh"], "secs": r["secs"], "thermal_wait": r["thermal_wait"], "prompt_tokens": r["prompt_tokens"],
           "gen_tokens": r["gen_tokens"], "num_predict": r["num_predict"], "done_reason": r["done_reason"]}
    if not got:
        score, files, fb = (False, -1), base, f"Your last reply did not contain any file as a line `### <file name>` followed by a ```{LANGS[st['language']][1]} code block."
        rec.update(code=False)
    else:
        files = {**base, **got}; os.makedirs(os.path.join(d, "code"), exist_ok=True)
        for n, code in files.items():
            with open(os.path.join(d, "code", n), "w") as f: f.write(code)
        try:
            built, passed, names, fb = evaluate(proj, st, files)
            if built and (st["best"] is None or passed + names >= _total(st["best"])):
                st["best"] = {"round": rnd, "draft": draft, "examples": passed, "declared": names, "files": sorted(files), "feedback": fb,
                              "commit": deliver(proj, st, files, rnd, draft, passed, names)}
        except (OSError, RuntimeError, subprocess.SubprocessError) as e: return {"event": "error", "error": f"could not judge or deliver round {rnd}: {e}"[:400]}    # the round is not counted
        score = (built, passed + names)
        rec.update(code=True, files=sorted(got), chars=sum(map(len, got.values())), built=built, examples=passed, declared=names, unchanged=bool(dbest) and files == dbest["files"])
        if built and r["done_reason"] != "length": st["max_complete"] = max(st.get("max_complete") or 0, r["gen_tokens"])
    if r["done_reason"] == "length": fb = "Your last reply was cut off because it was too long.\n\n" + fb
    improved = dbest is None or score > tuple(dbest["score"])
    if improved or score == tuple(dbest["score"]): dbest = {"score": list(score), "files": files, "feedback": fb, "round": rnd}     # continue from the draft's best version (latest among equals)
    stale = 0 if improved else stale + 1
    st.update(round=rnd, draft=draft, stale=stale, fresh=stale >= cfg["patience"], dbest=dbest, used_s=round(st["used_s"] + time.time() - t0, 1), model_errors=0)
    rec.update(improved=improved, stale=stale, restart_next=st["fresh"], used_s=st["used_s"])
    _save(proj, st); append_jsonl(dpath(proj, "rounds.jsonl"), rec)
    audit.log(proj, "drafts_round", **{k: rec.get(k) for k in ("round", "draft", "built", "examples", "gen_tokens", "done_reason")})
    if rec.get("built") and rec.get("examples") == st["examples_total"] and rec.get("declared", 0) == st.get("declared_total", 0): return finish(proj, st, "examples pass")
    return {"event": "round", "round": rnd, "draft": draft, "built": rec.get("built", False), "examples": f"{rec.get('examples', 0)}/{st['examples_total']}",
            "best": _best_text(st), "restart_next": st["fresh"], "minutes": round(st["used_s"] / 60, 1)}

def _total(b): return b["examples"] + b.get("declared", 0)
def complete(st):
    b = st["best"]
    return bool(b) and b["examples"] == st["examples_total"] and b.get("declared", 0) == st.get("declared_total", 0)

def _best_text(st):
    b, dt = st["best"], st.get("declared_total", 0)
    if not b: return "nothing builds yet"
    return f"{b['examples']}/{st['examples_total']} examples" + (f", {b.get('declared', 0)}/{dt} declared names" if dt else "") + f" (round {b['round']})"

def finish(proj, st, why):
    st["finished"] = {"stop": why, "at": now(), "minutes": round(st["used_s"] / 60, 1)}; _save(proj, st)
    with open(dpath(proj, "summary.json"), "w") as f: json.dump(summary(proj, st), f, indent=1)
    audit.log(proj, "drafts_finished", stop=why, best=st["best"] and st["best"]["round"])
    return final_event(st)

def final_event(st):
    b, total, dt = st["best"], st["examples_total"], st.get("declared_total", 0)
    return {"event": "all_done" if complete(st) else "finished_incomplete", "stop": st["finished"]["stop"], "delivered_round": b and b["round"],
            "examples": f"{b['examples'] if b else 0}/{total}", **({"declared": f"{b.get('declared', 0) if b else 0}/{dt}"} if dt else {}),
            "rounds": st["round"], "drafts": st["draft"], "minutes": st["finished"]["minutes"]}

# ---------- reading the state ----------
def rounds(proj):
    try:
        with open(dpath(proj, "rounds.jsonl")) as f: return [json.loads(l) for l in f if l.strip()]
    except OSError: return []

def summary(proj, st=None):
    st = st or load(proj)
    if not st: return {"started": False}
    rs = rounds(proj); b = st["best"]
    return {"started": True, "rounds": st["round"], "drafts": st["draft"], "stop": (st["finished"] or {}).get("stop"), "minutes": round(st["used_s"] / 60, 1),
            "examples_total": st["examples_total"], "delivered_round": b and b["round"], "delivered_draft": b and b["draft"], "delivered_examples": b["examples"] if b else None,
            "declared_total": st.get("declared_total", 0), "delivered_declared": b.get("declared", 0) if b else None, "complete": complete(st),
            "commit": b and b.get("commit"), "files": st["names"], "language": st["language"], "uncovered": st["uncovered"],
            "cut_off": sum(1 for r in rs if r.get("done_reason") == "length"), "no_code": sum(1 for r in rs if not r.get("code")),
            "repairs": sum(1 for r in rs if not r.get("fresh")), "unchanged": sum(1 for r in rs if r.get("unchanged")),
            "model_s": round(sum(r.get("secs") or 0 for r in rs), 1), "thermal_s": sum(r.get("thermal_wait") or 0 for r in rs),
            "gen_tokens": sum(r.get("gen_tokens") or 0 for r in rs), "settings": st["settings"]}

def status_line(proj):
    st = load(proj)
    if not st: return "drafts: not started"
    if st["finished"]: return f"drafts: finished ({st['finished']['stop']}), {_best_text(st)}, {st['round']} rounds in {st['draft']} drafts, {st['finished']['minutes']} min"
    return f"drafts: round {st['round']} (draft {st['draft']}), best {_best_text(st)}, {st['used_s'] / 60:.0f} of {st['settings']['minutes']:g} min"

def report(proj):
    """The morning report of a drafts project (markdown)."""
    st = load(proj)
    if not st: return f"# {proj.name}\n\nNot started yet (codebot start {proj.name}).\n"
    s, b, total = summary(proj, st), st["best"], st["examples_total"]
    L = [f"# codebot report: {proj.name}", "", f"- state: {'finished: ' + s['stop'] if s['stop'] else 'running'}; {s['rounds']} rounds in {s['drafts']} drafts, {s['minutes']} min "
         f"(model {s['model_s'] / 60:.1f} min, thermal pauses {s['thermal_s'] / 60:.1f} min)"]
    dt = st.get("declared_total", 0); names = f", {b.get('declared', 0)} of {dt} declared names defined" if b and dt else ""
    if b: L.append(f"- delivered: round {b['round']} (draft {b['draft']}): builds, **{b['examples']} of {total}** spec examples pass{names}; commit {(b.get('commit') or 'none')[:10]} in {proj.rundir}")
    else: L.append(f"- delivered: nothing (no version built); the code dir is as it was. Every round's code is in {dpath(proj, 'rounds')}")
    if st["uncovered"]: L.append(f"- **not checked**: requirement(s) {', '.join(map(str, st['uncovered']))} of SPEC.md have no examples, so nothing checked them. Review those parts yourself.")
    if not total: L.append("- **no examples** in SPEC.md: only the build was checked.")
    if s["cut_off"]: L.append(f"- {s['cut_off']} replies ran to the {st['settings']['num_predict']}-token limit and were cut off")
    if s["repairs"]: L.append(f"- {s['unchanged']} of {s['repairs']} repair rounds sent the code back unchanged")
    if b and b["examples"] < total and b.get("feedback"): L += ["", "What the delivered version still gets wrong:", "```", b["feedback"][-2500:], "```"]
    L += ["", f"Review: `codebot diff {proj.name}`", ""]
    return "\n".join(L)

def short(proj):
    st = load(proj)
    if not st: return "not started"
    b, total = st["best"], st["examples_total"]
    dt = st.get("declared_total", 0)
    got = (f"{b['examples']}/{total} spec examples pass" + (f", {b.get('declared', 0)}/{dt} declared names defined" if dt else "") + f" (round {b['round']})") if b else "no version built"
    unc = f"; requirement(s) {', '.join(map(str, st['uncovered']))} have no examples and were not checked" if st["uncovered"] else ""
    return f"{got}; {st['round']} rounds in {st['draft']} drafts, {st['used_s'] / 60:.0f} min{unc}."

def write_report(proj):
    path = proj.p(f"report-{time.strftime('%Y%m%d-%H%M%S')}.md")
    with open(path, "w") as f: f.write(report(proj))
    return path
