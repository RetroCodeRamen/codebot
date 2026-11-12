#!/usr/bin/env python3
"""Build the toolbox benchmarks (bench/ladder/catalog/toolbox_NN) from tools.py and the real tools on this machine.

  python3 bench/toolbox/make.py            # writes the catalog entries and validates them

For every example and case the real tool is run (LC_ALL=C, in a directory holding the fixtures) and what it printed and its exit status become
the expected values: the spec's examples and the hidden tests come from the same oracle, and none of them from a person. The reference
solution (bench/toolbox/reference) is a separate implementation written from the spec's text; `validate` requires it to pass every hidden test
and every spec example, which shows the text describes what the real tools do for the cases tested.
"""
import json, os, re, shlex, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.realpath(__file__)); HOME = os.path.realpath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [HERE, os.path.join(HOME, "lib")]
import tools as T                                                                # noqa: E402
from codebot import assertions as A, drafts                                      # noqa: E402
from codebot.capbench import manifests as M, reference as R                      # noqa: E402

def real(tool, args, stdin):
    """(stdout, exit status) of the real tool."""
    name = tool.get("real", tool["name"]); exe = shutil.which(name, path="/usr/bin:/bin")
    if not exe: raise SystemExit(f"the real {name} is not installed")
    d = tempfile.mkdtemp(prefix="cb-tb-")
    try:
        for n, text in T.FIXTURES.items():
            with open(os.path.join(d, n), "w", newline="") as f: f.write(text)
        p = subprocess.run([exe, *(["-F"] if name == "grep" else []), *args], input=(stdin or "").encode("latin-1"), capture_output=True, cwd=d, timeout=20,
                           env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"})
        return p.stdout.decode("latin-1"), p.returncode
    finally: shutil.rmtree(d, ignore_errors=True)

def esc(text): return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")

def sentence(tool, args, stdin):
    """One example as the spec writes it, in a form codebot's grounding reads."""
    out, rc = real(tool, args, stdin); cmd = f"python3 {tool['name']}.py" + (" " + shlex.join(args) if args else "")
    shown = out[:-1] if out.endswith("\n") else out
    if "`" in cmd + shown + (stdin or ""): raise SystemExit(f"a backtick cannot be written in an example: {cmd}")
    s = f"`{cmd}`"
    if stdin is not None: s += f" with standard input `{esc(stdin[:-1] if stdin.endswith(chr(10)) else stdin)}`"
    if shown == "": s += " prints nothing"
    elif stdin is not None or not ("\n" in shown or "\t" in shown): s += f" prints `{esc(shown) if stdin is not None else shown}`"
    else: s += f" prints the lines `{esc(shown)}`"
    return s + (f" and exits with status {rc}" if rc else "")

def spec(tools):
    parts = [T.INTRO]
    for i, t in enumerate(tools, 1):
        ex = "; ".join(sentence(t, a, s) for a, s in t["examples"])
        parts.append(f"{i}. {t['text']} Examples: {ex}.\n")
    return "\n".join(parts)

def test_file(tool, req):
    name = tool["name"]; rows = []
    for i, (args, stdin) in enumerate(tool["cases"], 1):
        out, rc = real(tool, args, stdin); slug = re.sub(r"[^a-z0-9]+", "_", " ".join(args).lower()).strip("_")[:40] or "no_arguments"
        rows.append(f"    def test_r{req:02d}_c{i:02d}_{slug}(self): self.check({args!r}, {stdin!r}, {out!r}, {rc})")
    return f'''"""Hidden tests of {name}.py. Every expected output and exit status was printed by the real `{tool.get("real", name)}` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {T.FIXTURES!r}

class Test{name.capitalize()}(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "{name}.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"{name}.py {{args}} with standard input {{stdin!r}}")
''' + "\n".join(rows) + "\n"

def build(ident, n):
    tools = T.TOOLS[:n]; d = os.path.join(M.catalog_dir(HOME), ident)
    shutil.rmtree(d, ignore_errors=True)
    for sub in ("seed", "reference_solution", "reference_tests"): os.makedirs(os.path.join(d, sub))
    with open(os.path.join(d, "SPEC.md"), "w") as f: f.write(spec(tools))
    for name, text in T.FIXTURES.items():
        with open(os.path.join(d, "seed", name), "w", newline="") as f: f.write(text)
    with open(os.path.join(d, "seed", "README.md"), "w") as f: f.write("# Workspace\n\nCommand-line text tools. See the task for what to build. The .txt and .csv files are test data.\n")
    for fn in ["common.py"] + [t["name"] + ".py" for t in tools]: shutil.copyfile(os.path.join(HERE, "reference", fn), os.path.join(d, "reference_solution", fn))
    for i, t in enumerate(tools, 1):
        with open(os.path.join(d, "reference_tests", f"test_{t['name']}.py"), "w") as f: f.write(test_file(t, i))
    manifest = {"id": ident, "level": None, "title": f"Toolbox: {n} command-line text tools", "version": "1.0.0",
                "difficulty": f"{n} small tools with exact output formats; the size is the point ({n} tools)",
                "primary_challenge": "The same kind of work at a growing size: where one context window stops being enough. Expected outputs are the real tools'",
                "spec": "SPEC.md", "seed": "seed", "reference_solution": "reference_solution", "reference_tests": "reference_tests",
                "expected_tasks": n, "planner": {"max_tasks": 40, "num_predict": 6000},
                "requirements": [{"id": f"R{i}", "title": t["name"], "keywords": [t["name"]]} for i, t in enumerate(tools, 1)]}
    with open(os.path.join(d, "manifest.json"), "w") as f: json.dump(manifest, f, indent=1)
    return d

def validate(ident, n):
    m = M.load(HOME, ident); problems = list(M.validate_manifest(m, HOME))
    r = R.validate_level(m, HOME); problems += r["problems"]
    sol = R.verify(m, M.solution_dir(m)); bad = [(k, v["msg"][:300]) for k, v in sol["tests"].items() if v["status"] != "pass"]
    build_cmd, checks = drafts.examples(M.spec_text(m)); want = sum(len(t["examples"]) for t in T.TOOLS[:n])
    if len(checks) != want: problems.append(f"the spec has {want} examples but codebot reads {len(checks)}")
    tmp = tempfile.mkdtemp(prefix="cb-tb-"); R.build_solution(m, tmp)
    with open(os.path.join(tmp, "_c.json"), "w") as f: json.dump({"asserts": checks}, f)
    p = subprocess.run([sys.executable, A.__file__, "_c.json"], cwd=tmp, capture_output=True, text=True); shutil.rmtree(tmp, ignore_errors=True)
    if p.returncode: problems.append("spec examples fail on the reference:\n" + p.stdout[-1500:])
    cov = drafts.coverage(M.spec_text(m))
    print(f"{ident}: {n} tools, spec {len(M.spec_text(m))} characters, {len(checks)} examples, files {len(cov['files'])}, hidden tests {sol['passed']}/{sol['total']} on the reference, "
          f"{r['seed']['passed']}/{r['seed']['total']} on the seed")
    for k, msg in bad[:12]: print("   FAIL", k, "\n      ", msg)
    for q in problems: print("   PROBLEM", q)
    return not problems and not bad

if __name__ == "__main__":
    ok = True
    for ident, n in T.SIZES.items(): build(ident, n); ok = validate(ident, n) and ok
    sys.exit(0 if ok else 1)
