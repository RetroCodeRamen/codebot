"""Writes bench/ladder/catalog/calc_go/reference_plan.json: one task per spec requirement. Each task starts with the build step and the spec's own
examples for that requirement (the same ones codebot's grounding adds), then hand-written checks of every rule the requirement states, with inputs
that differ from the hidden tests and the 20 hand inputs. Standard error is checked through python3 -c, because a run assertion cannot see it."""
import json, os

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "bench/ladder/catalog/calc_go/reference_plan.json")
BUILD = {"type": "run", "argv": ["go", "build", "-o", "calc", "."], "exit": 0}

def ok(expr, want): return {"type": "run", "argv": ["./calc", *expr.split()], "stdout": want}
def bad(expr, status): return {"type": "run", "argv": ["./calc", *expr.split()], "exit": status, "stdout": ""}
def stdin(text, want): return {"type": "run", "argv": ["./calc"], "stdin": text, "stdout": want}
def py(code, want): return {"type": "run", "argv": ["python3", "-c", code], "stdout": want}

def err_prefix(expr, status):
    args = repr(["./calc", *expr.split()])
    return py(f"import subprocess\np = subprocess.run({args}, capture_output=True, text=True)\n"
              "print('exit', p.returncode, '| stdout', repr(p.stdout), '| stderr starts with error:', p.stderr.startswith('error:'))\n",
              f"exit {status} | stdout '' | stderr starts with error: True")

TASKS = [
    (1, "Program calc builds with go build -o calc . and evaluates an expression given as arguments, one number or operator per argument; a single number is an expression",
     [ok("2 + 3", "5"), ok("7", "7"), ok("40 + 2", "42"), ok("19", "19"), {"type": "file_not_contains", "path": "go.mod", "text": "require"}]),
    (2, "The four operators + - * / and the letter x as another way to write *",
     [ok("6 x 7", "42"), ok("10 - 4", "6"), ok("9 / 3", "3"), ok("7 + 8", "15"), ok("3 - 9", "-6"), ok("5 * 6", "30"), ok("5 x 6", "30"), ok("18 / 4", "4.5")]),
    (3, "Precedence: * / x before + -, operators of the same kind applied left to right",
     [ok("2 + 3 * 4", "14"), ok("10 - 4 - 3", "3"), ok("8 / 4 / 2", "1"), ok("2 * 3 + 4 * 5", "26"), ok("20 - 12 / 4", "17"), ok("6 / 3 * 2", "4"),
      ok("1 - 2 + 3", "2"), ok("2 + 3 x 4 - 6 / 2", "11"), ok("9 - 3 - 2 - 1", "3")]),
    (4, "Numbers: integers, decimals and negatives, anything strconv.ParseFloat accepts except infinities and NaN (those are malformed, exit 2)",
     [ok("-2.5 + 1", "-1.5"), ok("0.1 + 0.2", "0.30000000000000004"), ok("-3 * -3", "9"), ok("7 - -2", "9"), ok("-1.5 x 4", "-6"), ok("2.5e2 + 0", "250"),
      ok(".25 + .25", "0.5"), ok("+4 - 1", "3"), ok("5. x 2", "10"), bad("nan + 1", 2), bad("1 + Inf", 2), bad("-infinity", 2)]),
    (5, "Output: one line ending in a newline, no trailing zeros and no exponent (strconv.FormatFloat(v, 'f', -1, 64)), negative zero printed as 0",
     [ok("2.5 * 2", "5"), ok("7 / 2", "3.5"), ok("1 / 3", "0.3333333333333333"), ok("1 / 8", "0.125"), ok("3 / 1000000", "0.000003"),
      ok("1e15 x 1000", "1000000000000000000"), ok("5 / 6", "0.8333333333333334"), ok("0 / -7", "0"), ok("-0.0", "0"),
      py("import subprocess\nprint(repr(subprocess.run(['./calc', '9', 'x', '9'], capture_output=True, text=True).stdout))\n", "'81\\n'")]),
    (6, "Errors: division by zero prints 'error: division by zero' to standard error and exits 1; a malformed expression prints a message starting with 'error:' to standard error and exits 2; nothing on standard output for either; success exits 0",
     [py("import subprocess\np = subprocess.run(['./calc', '8', '/', '0'], capture_output=True, text=True)\n"
         "print('exit', p.returncode, '| stdout', repr(p.stdout), '| stderr', repr(p.stderr.strip()))\n", "exit 1 | stdout '' | stderr 'error: division by zero'"),
      bad("2 x 3 / 0", 1), bad("5 / 0.0", 1), bad("6 - 4 / 0", 1),
      bad("4 % 2", 2), bad("five x 2", 2), bad("7 7", 2), bad("1 - x 2", 2), bad("- 4", 2), bad("6 /", 2), bad("x", 2),
      err_prefix("3 + + 1", 2), err_prefix("2 abc", 2), {"type": "run", "argv": ["./calc", "3", "-", "1"], "exit": 0, "stdout": "2"}]),
    (7, "No arguments: read expressions from standard input, one per line; print each result, or the error message (starting with error:) on standard output and continue; skip empty lines; exit 0 at the end",
     [stdin("3 + 4\n2 x 5\n", "7\n10"), stdin("6 - 2 * 2\n9\n", "2\n9"),
      py("import subprocess\np = subprocess.run(['./calc'], input='5 / 0\\n1 +\\n6 - 1\\n', capture_output=True, text=True)\nl = p.stdout.splitlines()\n"
         "print('exit', p.returncode, '|', len(l), 'lines |', [x.startswith('error:') for x in l[:2]], '|', l[2:])\n", "exit 0 | 3 lines | [True, True] | ['5']"),
      stdin("\n4 + 4\n\n  \n1 - 1\n", "8\n0"), stdin("  9   x  2 \n8 - 3", "18\n5"), {"type": "run", "argv": ["./calc"], "stdin": "", "exit": 0, "stdout": ""}]),
]

plan = {"goal": "Build the command-line calculator calc described in SPEC.md.",
        "context": "Go, standard library only. All code in package main in the root of the code directory (main.go); go build -o calc . produces the program calc.",
        "done": "Every requirement of SPEC.md holds.",
        "tasks": [{"title": title, "files": ["main.go"], "size": "M", "deps": [], "item": item, "asserts": [BUILD, *asserts]} for item, title, asserts in TASKS]}
with open(OUT, "w") as f: json.dump(plan, f, indent=1); f.write("\n")
print(OUT, sum(len(t["asserts"]) for t in plan["tasks"]), "assertions in", len(plan["tasks"]), "tasks")
