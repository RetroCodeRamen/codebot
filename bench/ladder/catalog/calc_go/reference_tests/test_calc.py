"""Hidden acceptance tests for the calc_go benchmark. Never shown to the agent. They build the program once with the real Go toolchain and run it
as a user would: arguments, standard input, standard output, standard error and the exit status. Test names carry the requirement (test_rNN_)."""
import atexit, os, shutil, subprocess, tempfile, unittest

GO = shutil.which("go") or "/usr/local/go/bin/go"
TMP = tempfile.mkdtemp(prefix="calc-ref-")
BIN = os.path.join(TMP, "calc")
BUILD = {"rc": None, "out": ""}
atexit.register(shutil.rmtree, TMP, True)

def build():
    """Build once, on first use (the benchmark runs each test on its own, so a module-level setUp hook would never run)."""
    if BUILD["rc"] is not None: return
    env = dict(os.environ, GOCACHE=os.path.join(TMP, "gocache"), GOPATH=os.path.join(TMP, "gopath"), GOTOOLCHAIN="local", GOFLAGS="-mod=mod", CGO_ENABLED="0")
    p = subprocess.run([GO, "build", "-o", BIN, "."], cwd=os.getcwd(), capture_output=True, text=True, timeout=240, env=env)
    BUILD.update(rc=p.returncode, out=(p.stdout + p.stderr)[-1500:])

def calc(*args, stdin=None):
    build()
    if BUILD["rc"] != 0: raise AssertionError(f"go build -o calc . failed: {BUILD['out']}")
    p = subprocess.run([BIN, *args], input=stdin if stdin is not None else "", capture_output=True, text=True, timeout=20)
    return p.returncode, p.stdout, p.stderr

class Base(unittest.TestCase):
    def ok(self, args, want):
        rc, out, err = calc(*args)
        self.assertEqual((rc, out), (0, want + "\n"), f"calc {' '.join(args)} (stderr: {err.strip()[:200]})")
    def fails(self, args, status):
        rc, out, err = calc(*args)
        self.assertEqual(rc, status, f"calc {' '.join(args)}: exit status (stdout {out!r}, stderr {err!r})")
        self.assertEqual(out, "", f"calc {' '.join(args)}: must print nothing to standard output")
        self.assertTrue(err.startswith("error:"), f"calc {' '.join(args)}: standard error must start with 'error:' (got {err!r})")

class TestBuildAndArguments(Base):
    def test_r01_go_build_produces_the_program(self): build(); self.assertEqual(BUILD["rc"], 0, BUILD["out"])
    def test_r01_examples_from_the_spec(self): self.ok(["2", "+", "3"], "5"); self.ok(["7"], "7")
    def test_r01_single_numbers(self):
        for a, w in (("0", "0"), ("42", "42"), ("-8", "-8"), ("3.25", "3.25")): self.ok([a], w)
    def test_r01_standard_library_only(self):
        with open("go.mod") as f: mod = f.read()
        self.assertNotIn("require", mod, "go.mod must not require third-party modules")

class TestOperators(Base):
    def test_r02_examples_from_the_spec(self): self.ok(["6", "x", "7"], "42"); self.ok(["10", "-", "4"], "6"); self.ok(["9", "/", "3"], "3")
    def test_r02_each_operator(self):
        for args, w in ((["1", "+", "1"], "2"), (["5", "-", "8"], "-3"), (["4", "*", "2.5"], "10"), (["1", "/", "4"], "0.25"), (["3", "x", "3"], "9")): self.ok(args, w)
    def test_r02_star_and_x_are_the_same(self):
        self.assertEqual(calc("12", "*", "12")[1], calc("12", "x", "12")[1]); self.ok(["12", "*", "12"], "144")

class TestPrecedence(Base):
    def test_r03_examples_from_the_spec(self):
        self.ok(["2", "+", "3", "*", "4"], "14"); self.ok(["10", "-", "4", "-", "3"], "3"); self.ok(["8", "/", "4", "/", "2"], "1"); self.ok(["2", "*", "3", "+", "4", "*", "5"], "26")
    def test_r03_division_before_subtraction(self): self.ok(["10", "-", "6", "/", "2"], "7"); self.ok(["1", "+", "9", "/", "3", "-", "2"], "2")
    def test_r03_left_to_right_for_the_same_kind(self):
        self.ok(["100", "/", "10", "*", "5"], "50"); self.ok(["2", "-", "3", "+", "4"], "3"); self.ok(["5", "-", "5", "-", "5", "-", "5"], "-10")
    def test_r03_longer_expressions(self):
        self.ok(["1", "+", "2", "*", "3", "-", "4", "/", "2", "+", "5", "x", "2"], "15"); self.ok(["2", "x", "2", "x", "2", "x", "2"], "16")

class TestNumbers(Base):
    def test_r04_examples_from_the_spec(self): self.ok(["-2.5", "+", "1"], "-1.5"); self.ok(["0.1", "+", "0.2"], "0.30000000000000004"); self.ok(["-3", "*", "-3"], "9")
    def test_r04_negative_operands_after_operators(self): self.ok(["5", "-", "-5"], "10"); self.ok(["-10", "/", "-4"], "2.5"); self.ok(["4", "+", "-1.5"], "2.5")
    def test_r04_forms_parsefloat_accepts(self): self.ok(["1e3", "+", "1"], "1001"); self.ok([".5", "+", ".5"], "1"); self.ok(["+2", "*", "3"], "6"); self.ok(["2.", "+", "1"], "3")
    def test_r04_infinity_and_nan_are_not_numbers(self):
        for t in ("inf", "NaN", "Infinity", "-Inf"): self.fails([t, "+", "1"], 2)

class TestFormatting(Base):
    def test_r05_examples_from_the_spec(self): self.ok(["2.5", "*", "2"], "5"); self.ok(["7", "/", "2"], "3.5"); self.ok(["1", "/", "3"], "0.3333333333333333")
    def test_r05_no_exponent_for_large_and_small_values(self):
        self.ok(["1e20", "*", "10"], "1000000000000000000000"); self.ok(["1", "/", "1000000"], "0.000001"); self.ok(["123456789", "*", "1000"], "123456789000")
    def test_r05_negative_zero_prints_zero(self): self.ok(["0", "*", "-1"], "0"); self.ok(["-0"], "0")
    def test_r05_exactly_one_line(self):
        rc, out, _ = calc("2", "+", "2"); self.assertEqual(out.count("\n"), 1); self.assertTrue(out.endswith("\n"))

class TestErrors(Base):
    def test_r06_division_by_zero(self):
        rc, out, err = calc("1", "/", "0"); self.assertEqual((rc, out), (1, "")); self.assertEqual(err.strip(), "error: division by zero")
    def test_r06_division_by_zero_inside_a_longer_expression(self): self.fails(["1", "+", "5", "/", "0"], 1); self.fails(["0", "/", "0"], 1)
    def test_r06_division_by_a_zero_written_differently(self): self.fails(["3", "/", "0.0"], 1); self.fails(["3", "/", "-0"], 1)
    def test_r06_unknown_tokens(self): self.fails(["2", "%", "3"], 2); self.fails(["two", "+", "3"], 2); self.fails(["2", "+", "three"], 2); self.fails(["2", "^", "3"], 2)
    def test_r06_two_numbers_or_two_operators_in_a_row(self): self.fails(["2", "3"], 2); self.fails(["2", "+", "+", "3"], 2); self.fails(["2", "*", "/", "3"], 2)
    def test_r06_starts_or_ends_with_an_operator(self): self.fails(["+", "2"], 2); self.fails(["2", "+"], 2); self.fails(["*"], 2); self.fails(["2", "*", "3", "-"], 2)
    def test_r06_success_exits_zero(self): self.assertEqual(calc("1", "+", "1")[0], 0)

class TestStdin(Base):
    def test_r07_reads_one_expression_per_line(self):
        rc, out, _ = calc(stdin="2 + 3\n10 / 4\n7\n"); self.assertEqual((rc, out), (0, "5\n2.5\n7\n"))
    def test_r07_precedence_and_x_in_lines(self):
        rc, out, _ = calc(stdin="2 + 3 * 4\n6 x 7\n"); self.assertEqual(out, "14\n42\n")
    def test_r07_errors_are_reported_on_stdout_and_it_continues(self):
        rc, out, _ = calc(stdin="1 / 0\n2 +\n4 * 2\n"); lines = out.splitlines()
        self.assertEqual(rc, 0); self.assertEqual(len(lines), 3, out); self.assertTrue(lines[0].startswith("error:")); self.assertTrue(lines[1].startswith("error:")); self.assertEqual(lines[2], "8")
    def test_r07_empty_lines_are_skipped(self):
        rc, out, _ = calc(stdin="\n1 + 1\n\n   \n2 * 2\n"); self.assertEqual(out, "2\n4\n")
    def test_r07_extra_spaces_and_no_final_newline(self):
        rc, out, _ = calc(stdin="  3   -   1  \n9 / 3"); self.assertEqual(out, "2\n3\n")
    def test_r07_empty_input_prints_nothing(self): self.assertEqual(calc(stdin=""), (0, "", ""))

if __name__ == "__main__": unittest.main()
