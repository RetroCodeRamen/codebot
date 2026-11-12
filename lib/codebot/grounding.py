"""Checks grounded in the specification itself.

1. examples(): the spec's own worked examples become assertions written by the harness, not the model, so they are sound by construction.
   Two kinds are recognised, both exact and unambiguous:
     `slugify("Hello World")` is `"hello-world"`          (also: returns / gives / equals / ==, and "`f(1)` and `f(2)` are `True`", and raises `ValueError`)
     `calc 2 + 3` prints `5`                               (a command line and its standard output; more in _command_example: a message on standard
                                                            error, "nothing to standard output", "exits with status N", "with standard input `...`")
   A call needs the function's module, read from "`textutil.py` provides `slugify(...)`"; a command needs a program the sandbox can run: python3 / python,
   a ./path, or a program the spec says how to build ("`go build -o calc .`"), in which case the build command runs first as its own assertion.
2. screen(): model-written assertions are dropped when they cannot hold for a program that follows the spec: they contradict themselves, a spec example
   or another assertion of the plan (same call or command line, different expected result), pass a flag or shell syntax the spec never mentions to a command that
   is expected to succeed, pack words the spec passes as separate arguments into one, or run Python code in a project that is not Python. A `go run`
   command of a program the spec says how to build is rewritten to run the built program (via_built), so an exit status `go run` hides is testable."""
import ast, collections, json, os, re, shlex, sys

SPAN = re.compile(r"`([^`\n]+)`")
IS = re.compile(r"^\s*(?:is|returns|gives|equals|==|->|→)\s*$")
ARE = re.compile(r"^\s*are\s*$")
JOIN = re.compile(r"^\s*(?:,|and|, and|or|, or)\s*$")
RAISES = re.compile(r"^\s*raises\s*$")
PRINTS = re.compile(r"^\s*prints\s*$")
PROVIDES = re.compile(r"`([A-Za-z_]\w*)\.py`\s+(?:also\s+)?(?:provides|defines|has)\s+`([A-Za-z_]\w*)\(")
BUILD = re.compile(r"^(?:go\s+build\b.*?\s-o\s+(\S+)|(?:gcc|cc|clang|g\+\+|clang\+\+)\b.*?\s-o\s+(\S+))")
PY = ("python", "python3")
SHELL_WORDS = {"echo", "printf", "cat", "bash", "sh", "|", "||", "&&", ";", "<", ">", ">>", "2>&1", "<<<"}

PAREN = re.compile(r"^\s*\(\s*$")

# A command example is read up to the end of its sentence (or the next command): its code spans become \x00k\x01 and these clauses are picked out.
SENTENCE_END = re.compile(r"[.;](?:\s|$)")
STDIN_C = re.compile(r"\b(?:with|given)\s+(?:the\s+)?(?:standard input|stdin)\s+\x00(\d+)\x01")
PRINT_C = re.compile(r"\bprints\s+(?:the\s+lines\s+)?(a\s+(?:message|line)\s+(?:starting|beginning)\s+with\s+)?\x00(\d+)\x01(\s+(?:to|on)\s+(?:standard (?:error|output)|stderr|stdout))?")
NOTHING_C = re.compile(r"\bnothing\s+(?:to|on)\s+(?:standard output|stdout)\b|\bprints\s+nothing\b(?!\s+(?:to|on)\s+(?:standard error|stderr))")
EXITS_C = re.compile(r"\bexits?\s+with\s+(?:status|code)\s+(\d+)\b")
ESC = re.compile(r"\\([nt\\])")

def _unescape(s): return ESC.sub(lambda m: {"n": "\n", "t": "\t", "\\": "\\"}[m.group(1)], s)

def spans(text):
    """[(code, text_before)] for every `code` span, text_before = the plain text since the previous span."""
    out, last = [], 0
    for m in SPAN.finditer(text):
        out.append((m.group(1), text[last:m.start()])); last = m.end()
    return out

def _after(text, k):
    """The plain text right after the k-th code span of text."""
    ms = list(SPAN.finditer(text))
    return text[ms[k].end():ms[k + 1].start() if k + 1 < len(ms) else len(text)]

def _jsonable(v):
    if isinstance(v, tuple): v = list(v)
    if isinstance(v, list): return [_jsonable(x) for x in v]
    if isinstance(v, dict): return {k: _jsonable(x) for k, x in v.items()} if all(isinstance(k, str) for k in v) else None
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))): return None
    if v is None or isinstance(v, (bool, int, float, str)): return v
    return None

def _literal(code):
    """(True, json-able value) for a Python literal, else (False, None). A tuple becomes a list (the runner compares a returned tuple to a list)."""
    try: v = ast.literal_eval(code.strip())
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError): return False, None
    j = _jsonable(v)
    return (j is not None or v is None), j

def _call(code):
    """(func, args, kwargs) for `name(literal, ..., key=literal)`, else None."""
    try: node = ast.parse(code.strip(), mode="eval").body
    except (SyntaxError, ValueError): return None
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)): return None
    args, kwargs = [], {}
    for a in node.args:
        if isinstance(a, ast.Starred): return None
        ok, v = _literal(ast.unparse(a))
        if not ok: return None
        args.append(v)
    for k in node.keywords:
        if k.arg is None: return None
        ok, v = _literal(ast.unparse(k.value))
        if not ok: return None
        kwargs[k.arg] = v
    return node.func.id, args, kwargs

def _declared(spec):
    """Functions the spec declares with a signature span such as `slugify(text)` or `truncate(text, width, suffix="...")` (bare parameter names, not
    values): only these can be called in an example, so `len(text) <= width` or `sorted(x)` in the prose never become checks."""
    out = set()
    for code, _ in spans(spec):
        try: node = ast.parse(code.strip(), mode="eval").body
        except (SyntaxError, ValueError): continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and all(isinstance(a, ast.Name) for a in node.args): out.add(node.func.id)
    return out

def modules(spec):
    """{function: module}: "`textutil.py` provides `slugify(...)`", or the only module the spec names (`Build `depgraph.py` ...`) for every function it
    declares. A function named for two modules is left out (ambiguous)."""
    seen = {}
    for mod, fn in PROVIDES.findall(spec): seen.setdefault(fn, set()).add(mod)
    named = {m.group(1) for m in re.finditer(r"`([A-Za-z_]\w*)\.py`", spec)}
    if len(named) == 1:
        for fn in _declared(spec): seen.setdefault(fn, set()).add(next(iter(named)))
    return {fn: next(iter(m)) for fn, m in seen.items() if len(m) == 1}

def build_step(spec):
    """(build argv, program name) when the spec says how the program is built (`go build -o calc .`), else (None, None)."""
    for code, _ in spans(spec):
        m = BUILD.match(code.strip())
        if m:
            try: argv = shlex.split(code)
            except ValueError: continue
            out = m.group(1) or m.group(2)
            return argv, out[2:] if out.startswith("./") else out
    return None, None

def _program(argv0, prog):
    """The argv[0] the sandbox can run for a command written in the spec, or None."""
    if argv0 in PY or argv0.startswith("./"): return argv0
    if prog and argv0 == prog: return "./" + prog
    return None

def _runnable(code, prog):
    try: argv = shlex.split(code)
    except ValueError: return None
    return _program(argv[0], prog) and argv if argv else None

def _command_example(item, i, prog):
    """(run assertion, index of the span after it, whether it needs the build) for a command example starting at code span i of item, or None:
         `calc 2 + 3` prints `5`
         `calc 1 / 0` prints `error: division by zero` to standard error, nothing to standard output, and exits with status 1
         `calc 2 +` prints a message starting with `error:` to standard error and exits with status 2
         `calc` with standard input `2 + 3\\n\\n1 / 0` prints `5\\nerror: division by zero` and exits with status 0
         `python3 cut.py -f1 table.csv` prints the lines `name\\npear`
    With standard input, or after "prints the lines", \\n and \\t in the input and the output are a line break and a tab (and the input ends with a
    line break). "Starting with" is checked as "contains"."""
    ms = list(SPAN.finditer(item)); argv = _runnable(ms[i].group(1), prog)
    if not argv: return None
    p = _program(argv[0], prog); parts, pos, k = [], ms[i].end(), i + 1
    while True:                                      # the example's text: up to the end of its sentence or the next command, spans as \x00k\x01
        gap = item[pos:ms[k].start() if k < len(ms) else len(item)]; end = SENTENCE_END.search(gap)
        if end: parts.append(gap[:end.start()]); break
        parts.append(gap)
        if k >= len(ms) or _runnable(ms[k].group(1), prog): break
        parts.append(f"\x00{k}\x01"); pos = ms[k].end(); k += 1
    text, val = "".join(parts), lambda n: ms[int(n)].group(1)
    a = {"type": "run", "argv": [p, *argv[1:]]}
    m = STDIN_C.search(text); lines = bool(m)
    if m: s = _unescape(val(m.group(1))); a["stdin"] = s if s.endswith("\n") else s + "\n"
    for m in PRINT_C.finditer(text):
        v = _unescape(val(m.group(2))) if lines or "the lines" in m.group(0) else val(m.group(2)); err = bool(m.group(3)) and "err" in m.group(3)
        if m.group(1) or err: a.setdefault("stderr_contains" if err else "stdout_contains", []).append(v)
        else: a["stdout"] = v
    if not (set(a) & {"stdout", "stdout_contains"}) and NOTHING_C.search(text): a["stdout"] = ""
    m = EXITS_C.search(text)
    if m: a["exit"] = int(m.group(1))
    if not set(a) & {"stdout", "stdout_contains", "stderr_contains", "exit"}: return None
    return a, k, prog is not None and p == "./" + prog

def examples(spec, item):
    """Assertions for the worked examples in one requirement (item) of the spec. The build step, if any, comes first."""
    mods, (build, prog) = modules(spec), build_step(spec)
    out, need_build, sp = [], False, spans(item)
    def add(a):
        if a not in out: out.append(a)
    def call_assert(code, value_code, raises=None):
        c = _call(code)
        if not c or c[0] not in mods: return
        fn, args, kwargs = c
        a = {"type": "call", "module": mods[fn], "func": fn, "args": args, **({"kwargs": kwargs} if kwargs else {})}
        if raises: a["raises"] = raises
        else:
            ok, v = _literal(value_code)
            if not ok: return
            a["expect"] = v
        add(a)
    i = 0
    while i < len(sp):
        code, _ = sp[i]
        nxt = sp[i + 1] if i + 1 < len(sp) else None
        if nxt and IS.match(nxt[1]): call_assert(code, nxt[0]); i += 2; continue
        if nxt and PAREN.match(nxt[1]) and _after(item, i + 1).lstrip().startswith(")") and _call(code):     # "`is_prime(1_000_000_007)` (`True`)"
            call_assert(code, nxt[0]); i += 2; continue
        if nxt and RAISES.match(nxt[1]) and re.fullmatch(r"[A-Z]\w*(?:Error|Exception)", nxt[0].strip()): call_assert(code, None, nxt[0].strip()); i += 2; continue
        ce = _command_example(item, i, prog)
        if ce: add(ce[0]); need_build = need_build or ce[2]; i = ce[1]; continue
        if nxt and PRINTS.match(nxt[1]): i += 2; continue                 # the output of a command the sandbox cannot run
        # "`f(1)`, `f(2)` and `f(3)` are `False`": a run of call spans joined by commas/"and", then "are" + value
        j = i
        while j + 1 < len(sp) and JOIN.match(sp[j + 1][1]) and _call(sp[j + 1][0]): j += 1
        if j + 1 < len(sp) and ARE.match(sp[j + 1][1]) and _call(sp[i][0]):
            for k in range(i, j + 1): call_assert(sp[k][0], sp[j + 1][0])
            i = j + 2; continue
        i += 1
    if need_build: out.insert(0, {"type": "run", "argv": build, "exit": 0})
    return out

def by_item(spec):
    """[example assertions of requirement 1, of requirement 2, ...]."""
    from .specdoc import item_texts
    return [examples(spec, it) for it in item_texts(spec)]

def all_examples(spec):
    """Every example assertion of the spec, each once (the build step appears once)."""
    out = []
    for ex in by_item(spec):
        out += [a for a in ex if a not in out]
    return out

# ---------- screening model-written assertions ----------
FLAG = re.compile(r"^--?[A-Za-z][\w-]*$")
LANG_FILES = ("go.mod", "Cargo.toml", "package.json", "pom.xml", "build.gradle", "CMakeLists.txt")
LANG_EXT = (".go", ".rs", ".c", ".cc", ".cpp", ".java", ".js", ".ts", ".rb", ".cs")

def project_kind(spec, rundir=None):
    """'python', or 'other' when there is positive evidence of another language (a go.mod, .go/.rs/... sources, a language named in the spec) and
    none of Python (the spec never says Python or .py, the code directory has no .py file)."""
    py = bool(re.search(r"\bpython\b|\.py\b", spec, re.I)); other = bool(re.search(r"\b(?:Go|Golang|Rust|Java|JavaScript|TypeScript|Node\.js|C\+\+|C#|Ruby)\b", spec))
    if rundir and os.path.isdir(rundir):
        seen = 0
        for dp, dn, fn in os.walk(rundir):
            dn[:] = [d for d in dn if not d.startswith(".") and d not in ("node_modules", "vendor", "target", "__pycache__")]
            for f in fn:
                seen += 1
                if f.endswith(".py"): py = True
                elif f in LANG_FILES or f.endswith(LANG_EXT): other = True
            if seen > 2000 or dp.count(os.sep) - rundir.count(os.sep) >= 2: dn[:] = []
    return "other" if other and not py else "python"

def spec_tokens(spec):
    """Every whitespace-separated word of the spec, as written and with surrounding quotes/backticks/punctuation removed."""
    out = set()
    for t in re.split(r"\s+", spec):
        out.add(t); out.add(t.strip("`'\"()[],.;:!?"))
    return out

def go_run_args(argv):
    """The program's arguments when argv runs the Go program of the code directory (`go run . ARGS`, `go run main.go calc.go ARGS`), else None."""
    if argv[:2] != ["go", "run"]: return None
    if argv[2:3] in (["."], ["./"]): return argv[3:]
    i = 2
    while i < len(argv) and argv[i].endswith(".go") and "/" not in argv[i]: i += 1
    return argv[i:] if i > 2 else None

def _program_args(argv, prog=None):
    """The arguments the program under test receives (`go run . ARGS`, `./calc ARGS`, `python3 tool.py ARGS`), or None when the command runs some
    other tool (`go build -o calc .`, `gofmt -l .`, `python3 -c ...`), whose options the spec has no reason to mention."""
    g = go_run_args(argv)
    if g is not None: return g
    if (prog and argv[0] in (prog, "./" + prog)) or argv[0].startswith("./"): return argv[1:]
    if argv[0] in PY and len(argv) > 1 and argv[1].endswith(".py"): return argv[2:]
    return None

def via_built(a, spec):
    """`go run . ARGS` -> `./calc ARGS` when the spec says how the program is built with Go (`go build -o calc .`), unless the assertion expects exit
    status 1. `go run` reports every failing exit status as 1 (verified with Go 1.24), so a malformed-input check expecting status 2 can only ever hold
    against the built program (2026-10-17: dropping those instead left the calculator's exit statuses untested, and it exited 1 for every error); a
    success through `go run` is a success of the built program, and one form keeps the checks of one input comparable. Status 1 through `go run`
    means 'the program failed', with any status, so it is left as it is. Returns a (the same object) when nothing changes."""
    if not isinstance(a, dict) or a.get("type") != "run" or a.get("exit", 0) == 1 or not isinstance(a.get("argv"), list): return a
    args = go_run_args(a["argv"])
    if args is None: return a
    build, prog = build_step(spec)
    if not build or build[0] != "go": return a
    return {**a, "argv": ["./" + prog, *args]}

def _ungrounded(a, toks, prog=None):
    """Why a command expected to SUCCEED passes its program something no program following the spec accepts: an option or shell syntax the spec
    never mentions, or the program's own name as an argument (`go run . calc 2 + 3`)."""
    if a.get("type") != "run" or a.get("exit", 0) != 0 or not a.get("argv"): return None
    for t in _program_args(a["argv"], prog) or []:
        if prog and t in (prog, "./" + prog): return f"the program's own name {t!r} is passed to it as an argument"
        if t in toks: continue
        if FLAG.match(t): return f"the option {t!r} appears nowhere in the specification"
        if t in SHELL_WORDS: return f"{t!r} is shell syntax or a shell command, but argv goes to the program as it is, without a shell (standard input is the stdin field)"
    return None

def _named_in_stdin(a, prog=None):
    """Why an assertion's standard input is not what it means: a line of it starts with the program's own name, as a typed command line would.
    2026-10-18 planning probe: `./calc` with standard input `calc 2 + 3 * 4`, expecting 14 (the program reads `calc` as a token that is no number).
    The same mistake as the program's own name in argv (_ungrounded); no earlier plan had used stdin at all."""
    if a.get("type") != "run" or not prog or not isinstance(a.get("stdin"), str): return None
    line = next((l.strip() for l in a["stdin"].splitlines() if l.split()[:1] in ([prog], ["./" + prog])), None)
    return None if line is None else f"a line of its standard input starts with the program's own name ({line!r}), as a typed command line would"

NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")

def spec_commands(spec, prog=None):
    """[(arguments, command line as written)] for every command line of the program under test the spec shows (`calc 2 + 3` -> ["2", "+", "3"])."""
    out = []
    for code, _ in spans(spec):
        try: argv = shlex.split(code)
        except ValueError: continue
        args = _program_args(argv, prog) if argv else None
        if args is not None: out.append((args, code.strip()))
    return out

def _joined(a, cmds, prog=None):
    """Why a command expected to SUCCEED packs into one argument several words that the spec passes as separate arguments (`go run . "2 + 3"` where
    the spec writes `calc 2 + 3`): when every command line of the spec gives the program one word per argument and some give it several, a program
    that follows the spec reads "2 + 3" as one word, which it does not accept. 2026-10-17: all 12 checks of the calculator's standard-input task were
    like that (the model meant standard input), so the task could never pass. Every word must be one the spec passes as an argument (or a number,
    when the spec passes numbers). Not for a spec that quotes an argument with spaces itself (pyutils: `python3 cli.py slug "Hello World"`); a spec
    whose program joins its arguments and never quotes one would lose a sound check here, and no spec of the corpus is like that."""
    if a.get("type") != "run" or a.get("exit", 0) != 0 or not a.get("argv"): return None
    args = _program_args(a["argv"], prog)
    if not args or not any(len(c) >= 2 for c, _ in cmds) or any(not t.strip() or re.search(r"\s", t) for c, _ in cmds for t in c): return None
    seen = {t for c, _ in cmds for t in c}; nums = any(NUMBER.match(t) for t in seen)
    for t in args:
        words = t.split()
        if len(words) >= 2 and all(w in seen or (nums and NUMBER.match(w)) for w in words):
            shown = next(code for c, code in cmds if len(c) >= 2)
            return (f"its argument {t!r} holds several words, but the specification passes each such word as an argument of its own (`{shown}`), so a program "
                    f"that follows it reads {t!r} as one word; pass one word per argument, and put input the program reads from standard input in the stdin field")
    return None

def _unobservable(a):
    """Why the expected result can never be seen, whatever the program does. `go run` reports every failing exit status as 1 (verified with Go 1.24:
    a program that exits 2 makes `go run .` exit 1), so an exit status other than 0 or 1 through it is impossible. (screen() first rewrites such a
    command to run the built program when the spec says how to build it, see via_built; this drops the rest.)"""
    av = a.get("argv") or []
    if a.get("type") == "run" and av[:2] == ["go", "run"] and a.get("exit", 0) not in (0, 1):
        return f"`go run` exits with status 1 whenever the program fails, so exit status {a.get('exit')} can never be seen (run the built program instead)"
    return None

def _imports(code):
    try: tree = ast.parse(code)
    except (SyntaxError, ValueError): return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import): out += [x.name.split(".")[0] for x in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level: out.append(n.module.split(".")[0])
    return out

def _python_in_other(a):
    if a.get("type") == "call": return "a call assertion imports a Python module, but this project is not written in Python"
    av = a.get("argv") or []
    if a.get("type") != "run" or not av or av[0] not in PY or len(av) < 2: return None
    if av[1].endswith(".py"): return f"it runs the Python file {av[1]}, but this project is not written in Python"
    if av[1] == "-c" and len(av) > 2:
        own = [m for m in _imports(av[2]) if m not in sys.stdlib_module_names]
        if own: return f"its Python code imports {own[0]!r}, a module of the project, but this project is not written in Python"
    return None

def stderr_only(spec):
    """Texts the spec sends to standard error and never to standard output ("prints `error: division by zero` to standard error"): expecting one of
    them on standard output is a mistake (see _on_wrong_stream)."""
    err, out = set(), set()
    for m in re.finditer(r"`([^`\n]+)`\)?\s+(?:to|on)\s+(?:the\s+)?(standard error|stderr|standard output|stdout)\b", spec, re.I):
        (err if m.group(2).lower() in ("standard error", "stderr") else out).add(m.group(1).strip())
    return err - out

def _strike(a, err_only):
    """True when a run assertion expects, on standard output, a text the spec sends only to standard error."""
    if a.get("type") != "run" or not err_only: return False
    wanted = [s.strip() for s in _contains(a)] + ([a["stdout"].strip()] if isinstance(a.get("stdout"), str) else [])
    return any(t and any(t == x or x in t for x in err_only) for t in wanted)

def _contradictory(a):
    """Why a run assertion can hold for no program at all: standard output must be exactly S and also contain a text S does not contain. The runner
    compares stdout stripped, and a contained text, stripped, lies inside the stripped output; so the rule is sound by construction and needs no
    spec. 2026-10-18 calc4: four of T6's six checks were `"stdout": ""` with `"stdout_contains": ["error:"]`, so T6 could never pass."""
    if a.get("type") != "run" or not isinstance(a.get("stdout"), str): return None
    s = a["stdout"].strip(); bad = next((c.strip() for c in _contains(a) if c.strip() and c.strip() not in s), None)
    return None if bad is None else f"it wants standard output to be exactly {s!r} and also to contain {bad!r}, which no program can do"

def _on_wrong_stream(a, err_only):
    """Why an assertion expects on standard output a text the spec sends to standard error. Not for input on stdin (a spec may print errors on
    standard output in that mode) nor when the command merges the two streams (2>&1). Every such assertion in the corpus is impossible (5 of 5)."""
    if "stdin" in a or any("2>&1" in str(x) for x in a.get("argv") or []) or not _strike(a, err_only): return None
    return "it expects on standard output a text the specification sends to standard error"

def with_build(asserts, spec):
    """A check that runs the program the spec says how to build starts by building it, so it never judges a stale binary (the built program is
    usually git-ignored, so it survives a reset to the last checkpoint and an edit that no check rebuilt)."""
    build, prog = build_step(spec)
    if not build or not isinstance(asserts, list): return asserts
    step = {"type": "run", "argv": list(build), "exit": 0}
    if not any(isinstance(a, dict) and a.get("type") == "run" and (a.get("argv") or [None])[0] in (prog, "./" + prog) for a in asserts): return asserts
    return [step] + [a for a in asserts if a != step]

def invocation(a, prog=None):
    """What an assertion exercises: the same call, or the same command line and standard input (./calc and calc are one program; `go run .` is kept
    apart because it reports every failure as exit status 1)."""
    t = a.get("type")
    if t == "call": return ("call", a.get("module"), a.get("func"), json.dumps(a.get("args", []), sort_keys=True), json.dumps(a.get("kwargs", {}), sort_keys=True))
    av = a.get("argv")
    if t != "run" or not isinstance(av, list) or not av: return None
    if go_run_args(av) is not None: key = ("<go run>", *go_run_args(av))
    elif prog and av[0] in (prog, "./" + prog): key = ("<program>", *av[1:])
    else: key = tuple(av)
    return ("run", key, a.get("stdin") or "")

def _contains(a):
    c = a.get("stdout_contains"); return [c] if isinstance(c, str) else [s for s in c or [] if isinstance(s, str)]

def compatible(a, b):
    """False when a and b exercise the same thing and cannot both hold."""
    from .assertions import _same
    if a.get("type") == "call":
        ra, rb = a.get("raises"), b.get("raises")
        if ra or rb: return bool(ra and rb) and (ra == rb or {"Exception", "BaseException"} & {ra, rb} != set())
        return _same(a.get("expect"), b.get("expect"))
    if a.get("exit", 0) != b.get("exit", 0): return False
    for x, y in ((a, b), (b, a)):
        if isinstance(x.get("stdout"), str):
            out = x["stdout"].strip()
            if isinstance(y.get("stdout"), str) and y["stdout"].strip() != out: return False
            if any(s.strip() and s.strip() not in out for s in _contains(y)): return False
            if "stdout_json" in y:
                try:
                    if not _same(json.loads(out), y["stdout_json"]): return False
                except ValueError: return False
    if "stdout_json" in a and "stdout_json" in b and not _same(a["stdout_json"], b["stdout_json"]): return False
    return True

def _sig(a):
    """The expected result alone, normalised, for a majority vote among assertions of the same invocation."""
    e = {k: v for k, v in a.items() if k not in ("type", "argv", "module", "func", "args", "kwargs", "stdin")}
    if a.get("type") == "run": e["exit"] = a.get("exit", 0)
    if isinstance(e.get("stdout"), str): e["stdout"] = e["stdout"].strip()
    return json.dumps(e, sort_keys=True)

def _short(a, n=110):
    s = json.dumps(a); return s if len(s) <= n else s[:n] + "..."

def screen(groups, spec, kind="python", prepend=None, spec_examples=(), context=(), label="T"):
    """Drop model-written assertions that cannot hold for a program that follows the spec. Returns (new groups, notes, {group index: [reasons]}).
    groups: one assertion list per task, after repair and salvage. prepend: per task, the spec-example assertions the harness adds (first in the
    task, never dropped). spec_examples: every example of the spec (none may be contradicted). context: assertions already in force elsewhere (a
    split's parent, finished tasks), which a group may not contradict either; they are never dropped here. label: how notes name a group.
    Rules: a `go run` command of a program the spec says how to build with Go is first rewritten to run the built program (via_built); an exact
    duplicate is merged; an assertion that contradicts itself (stdout exactly S, and containing a text S does not contain) is dropped; Python code
    in a project that is not Python is dropped; so is standard input with a line that starts with the program's own name; a command expected to succeed that passes an option or shell syntax
    the spec never mentions, or the program's own name, or packs words the spec passes as separate arguments into one argument, is dropped; an exit
    status `go run` can never report is dropped; text the spec sends to standard error expected on standard output is dropped; an assertion that
    contradicts a spec example or the context is dropped; assertions of the groups that exercise the same call or command line with results that
    cannot all hold are dropped, except those that agree with a strict majority of them (a tie drops every assertion in the clash: at least one of
    them is wrong and nothing says which). A group that runs the program the spec says how to build starts with that build step (with_build)."""
    prepend = prepend or [[] for _ in groups]; _, prog = build_step(spec); toks = spec_tokens(spec); err_only = stderr_only(spec); cmds = spec_commands(spec, prog)
    notes, why, gone = [], collections.defaultdict(list), {}
    stage = []; rewritten = 0
    for gi, (g, ex) in enumerate(zip(groups, prepend)):
        keys = {json.dumps(e, sort_keys=True) for e in ex}; kept = []
        for a in g if isinstance(g, list) else []:
            if not isinstance(a, dict): kept.append(a); continue
            b = via_built(a, spec)
            if b is not a: rewritten += 1; a = b
            k = json.dumps(a, sort_keys=True)
            if k in keys: continue
            keys.add(k)
            r = (_contradictory(a) or (_python_in_other(a) if kind == "other" else None) or _ungrounded(a, toks, prog) or _named_in_stdin(a, prog)
                 or _joined(a, cmds, prog) or _unobservable(a) or _on_wrong_stream(a, err_only))
            if r: notes.append(f"{label}{gi + 1}: dropped {_short(a)}: {r}"); why[gi].append(r); continue
            kept.append(a)
        stage.append(kept)
    if rewritten: notes.append(f"rewrote {rewritten} `go run` assertion(s) to run the built program ./{prog}: `go run` reports every failing exit status as 1")
    authority = collections.defaultdict(list)
    for src, items in (("the specification's own example", [*spec_examples, *(e for ex in prepend for e in ex)]), ("an assertion already in force", context)):
        for e in items:
            e = via_built(e, spec)
            inv = invocation(e, prog) if isinstance(e, dict) else None
            if not inv: continue
            authority[inv].append((src, e))
            if inv[0] == "run" and inv[1][:1] == ("<program>",) and e.get("exit", 0) == 0:
                authority[("run", ("<go run>", *inv[1][1:]), inv[2])].append((src, e))       # a success looks the same through `go run .`
    by = collections.defaultdict(list)
    for gi, g in enumerate(stage):
        for ai, a in enumerate(g):
            inv = invocation(a, prog) if isinstance(a, dict) else None
            if inv is None: continue
            against = next((src for src, e in authority.get(inv, []) if not compatible(a, e)), None)
            if against: gone[(gi, ai)] = f"it contradicts {against} for the same input"; continue
            by[inv].append((gi, ai, a))
    for inv, members in by.items():
        if len(members) < 2: continue
        clash = [m for m in members if any(not compatible(m[2], o[2]) for o in members if o is not m)]
        if not clash: continue
        sig, n = collections.Counter(_sig(m[2]) for m in members).most_common(1)[0]
        losers = [m for m in members if not compatible(m[2], next(o[2] for o in members if _sig(o[2]) == sig))] if n * 2 > len(members) else clash
        for gi, ai, _ in losers: gone[(gi, ai)] = f"it and {len(members) - 1} other assertion(s) of the plan expect results for the same input that cannot all hold"
    out = []
    for gi, (g, ex) in enumerate(zip(stage, prepend)):
        kept = []
        for ai, a in enumerate(g):
            if (gi, ai) in gone: notes.append(f"{label}{gi + 1}: dropped {_short(a)}: {gone[(gi, ai)]}"); why[gi].append(gone[(gi, ai)])
            else: kept.append(a)
        out.append(with_build(list(ex) + kept, spec))
    return out, notes, dict(why)
