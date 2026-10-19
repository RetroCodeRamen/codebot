"""Hand inputs NOT in the hidden tests: build each copy with go and run it like a user would. Usage: handtest.py DIR [DIR ...]"""
import os, subprocess, sys, tempfile

GO = "/usr/local/go/bin/go"
# (label, args or None, stdin or None, expected (rc, stdout) ; stdout None = only check rc and that stderr starts with "error:")
CASES = [
    ("basic add",          ["8", "+", "5"], None, (0, "13\n")),
    ("basic sub",          ["3", "-", "10"], None, (0, "-7\n")),
    ("basic mul *",        ["6", "*", "9"], None, (0, "54\n")),
    ("basic mul x",        ["11", "x", "11"], None, (0, "121\n")),
    ("basic div",          ["22", "/", "8"], None, (0, "2.75\n")),
    ("single number",      ["15"], None, (0, "15\n")),
    ("decimals",           ["1.5", "+", "2.25"], None, (0, "3.75\n")),
    ("negative operand",   ["-4", "x", "2.5"], None, (0, "-10\n")),
    ("precedence 1",       ["3", "+", "4", "x", "5"], None, (0, "23\n")),
    ("precedence 2",       ["20", "-", "8", "/", "4"], None, (0, "18\n")),
    ("left to right",      ["12", "/", "3", "/", "2"], None, (0, "2\n")),
    ("long chain",         ["1", "+", "2", "+", "3", "+", "4", "x", "10"], None, (0, "46\n")),
    ("big result",         ["99999", "*", "99999"], None, (0, "9999800001\n")),
    ("third",              ["2", "/", "3"], None, (0, "0.6666666666666666\n")),
    ("div by zero",        ["5", "/", "0"], None, (1, "")),
    ("letters",            ["abc"], None, (2, "")),
    ("bad operator",       ["4", "&", "2"], None, (2, "")),
    ("dangling op",        ["9", "-"], None, (2, "")),
    ("stdin two lines",    [], "4 * 4\n9 - 10\n", (0, "16\n-1\n")),
    ("stdin error line",   [], "8 / 0\n3 + 3\n", (0, None)),
]

def build(d):
    out = os.path.join(tempfile.mkdtemp(prefix="hb-"), "calc")
    env = dict(os.environ, GOCACHE=os.path.join(os.path.dirname(out), "gc"), GOPATH=os.path.join(os.path.dirname(out), "gp"), GOTOOLCHAIN="local", GOFLAGS="-mod=mod", CGO_ENABLED="0")
    p = subprocess.run([GO, "build", "-o", out, "."], cwd=d, capture_output=True, text=True, env=env, timeout=240)
    return (out, None) if p.returncode == 0 else (None, (p.stdout + p.stderr).strip()[-300:])

for d in sys.argv[1:]:
    name = os.path.basename(d.rstrip("/")); exe, err = build(d)
    if not exe: print(f"## {name}: DOES NOT BUILD: {err}\n"); continue
    ok = 0; lines = []
    for label, args, stdin, (rc_w, out_w) in CASES:
        try: p = subprocess.run([exe, *args], input=stdin if stdin is not None else "", capture_output=True, text=True, timeout=10); rc, out, er = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired: rc, out, er = "timeout", "", ""
        if label == "stdin error line": good = rc == 0 and out.splitlines()[:1] and out.splitlines()[0].startswith("error:") and out.splitlines()[1:2] == ["6"]
        elif rc_w != 0: good = rc == rc_w and out == "" and er.startswith("error:")
        else: good = rc == rc_w and out == out_w
        ok += bool(good)
        lines.append(f"  {'PASS' if good else 'FAIL'} {label:18} calc {' '.join(args) or '<stdin ' + repr(stdin) + '>'} -> rc={rc} out={out!r} err={er.strip()[:60]!r}")
    print(f"## {name}: {ok}/{len(CASES)} hand inputs"); print("\n".join(lines)); print()
