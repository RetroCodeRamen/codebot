"""Drafts mode (lib/codebot/drafts.py): the whole program from the spec, judged by the spec's own examples, with restarts and the best version kept.
Tested with a scripted fake model: the prompts and the judging must stay the text of bench/baselines/bare_py.py (the measured loop), the round logic
must restart and deliver exactly as it did, and the supervisor, the CLI and the benchmark must drive it end to end."""
import json, os, shutil, subprocess, sys, time, unittest
from helpers import Sandboxed
from fake_ollama import FakeOllama
from codebot import drafts as D, ollama
from codebot.config import Project
from test_v_capbench import CapCase                           # the capbench test home (a base class: it has no tests of its own)

REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
CAT = os.path.join(REPO, "bench", "ladder", "catalog")
sys.path.insert(0, os.path.join(REPO, "bench", "baselines"))

SPEC = """# Doubler

Build `mathx.py` in Python 3 (standard library only).

## Requirements

1. `mathx.py` provides `double(n)`, which returns twice `n`: `double(2)` is `4` and `double(-3)` is `-6`.
2. `mathx.py` provides `half(n)`, which returns `n / 2` as a float.
"""
GOOD = "### mathx.py\n```python\ndef double(n):\n    return 2 * n\n\ndef half(n):\n    return n / 2\n```\n"
HALF = "### mathx.py\n```python\ndef double(n):\n    return n + 2\n```\n"           # double(2) holds, double(-3) does not
BROKEN = "### mathx.py\n```python\ndef double(n)\n    return 2 * n\n```\n"
def read(path):
    with open(path) as f: return f.read()
def module_reply(sol, names): return "\n".join(f"### {n}\n```python\n{read(os.path.join(sol, n))}```" for n in names)

class TestExtract(unittest.TestCase):
    N = ["fileutil.py", "textutil.py"]
    def test_headers_fences_and_comments_name_the_files(self):
        cases = [("### fileutil.py\n```python\nA\n```\n### textutil.py\n```python\nB\n```", {"fileutil.py": "A\n", "textutil.py": "B\n"}),
                 ("File: fileutil.py\n```python\nA\n```", {"fileutil.py": "A\n"}),            # the first version read this as util.py
                 ("**`textutil.py`**\n```python\nB\n```", {"textutil.py": "B\n"}),
                 ("```python fileutil.py\nA\n```", {"fileutil.py": "A\n"}),
                 ("```python\n# textutil.py\nB\n```", {"textutil.py": "# textutil.py\nB\n"}),
                 ("### fileutil.py\n```python\nold\n```\nBetter:\n### fileutil.py\n```python\nnew\n```", {"fileutil.py": "new\n"}),   # the last block wins
                 ("### fileutil.py\n```python\nA\n```\n### textutil.py\n```python\ncut off", {"fileutil.py": "A\n", "textutil.py": "cut off"}),
                 ("### fileutil.py\n```python\nA\n```\nWait, a better one:\n### fileutil.py\n```python\nhalf a rewr", {"fileutil.py": "A\n"}),   # a cut-off rewrite does not replace a complete block
                 ("```python\nA\n```", {}),                                                   # two files: an unnamed block is nobody's
                 ("### helper.py\n```python\nA\n```", {})]                                    # only the spec's modules are taken
        for reply, want in cases: self.assertEqual(D.extract(reply, self.N, "Python"), want, reply)
    def test_one_module_takes_an_unnamed_block_unless_another_file_is_named(self):
        self.assertEqual(D.extract("```python\nA\n```", ["m.py"], "Python"), {"m.py": "A\n"})
        self.assertEqual(D.extract("import os\nx = 1\n", ["m.py"], "Python"), {"m.py": "import os\nx = 1\n"})
        self.assertEqual(D.extract("### README.md\n```\nhello\n```", ["m.py"], "Python"), {})
        self.assertEqual(D.extract("```bash\npython3 m.py\n```", ["m.py"], "Python"), {})
    def test_a_spec_that_names_no_file_takes_any_file_of_its_language(self):
        r = "### main.go\n```go\npackage main\n```\n### go.mod\n```\nmodule calc\n```\n```bash\ngo build\n```"
        self.assertEqual(D.extract(r, [], "Go"), {"main.go": "package main\n"})
        self.assertEqual(D.extract("```go\npackage main\n```", [], "Go"), {"main.go": "package main\n"})
        self.assertEqual(D.extract("### lexer.go\n```go\npackage main\n```\n### main.go\n```go\npackage main\n```", [], "Go"), {"lexer.go": "package main\n", "main.go": "package main\n"})

class TestParity(unittest.TestCase):
    """The prompts and the judging are bench/baselines/bare_py.py's text (the measured loop), except the first prompt's last sentence, which names the
    files to write and asks for no other (asked for "every file", the model rewrote README.md and go.mod first)."""
    @classmethod
    def setUpClass(cls): import bare_py; cls.P = bare_py
    def test_files_examples_and_prompts_are_the_measured_ones(self):
        for lv in ("level_01", "level_03"):
            L = self.P.load(lv); spec = L["spec"]; shown, named = D._listing(os.path.join(CAT, lv, "seed"))
            self.assertEqual(D.spec_files(spec), L["files"]); self.assertEqual(D.examples(spec), (None, L["checks"]))
            self.assertEqual(D.system_prompt("Python"), self.P.SYSTEM)
            measured = self.P.first_prompt(L); old = f"every file ({', '.join(L['files'])})."
            self.assertTrue(measured.endswith(old))
            self.assertEqual(D.first_prompt(spec, L["files"], shown, named), measured[:-len(old)] + D._join(L["files"]) + ", and no other file.")
            files = {n: f"# {n}\n" for n in L["files"]}
            whole = module_reply(os.path.join(CAT, lv, "reference_solution"), L["files"])
            self.assertEqual(D.extract(whole, L["files"], "Python"), self.P.extract(whole, L["files"]))
            for fb in ("These files are missing: x.py.", "All files compile, but 1 of 6 examples from the specification fail:\nFAIL: f(1)"):
                self.assertEqual(D.repair_prompt(spec, {**shown, **files}, named, fb), self.P.repair_prompt(L, files, fb))
            self.assertEqual(D.repair_prompt(spec, {}, named, "x"), self.P.repair_prompt(L, {}, "x"))
    def test_a_spec_that_names_no_file_asks_for_the_source_files_only(self):
        spec = read(os.path.join(CAT, "calc_go", "SPEC.md")); shown, named = D._listing(os.path.join(CAT, "calc_go", "seed"))
        self.assertEqual((sorted(shown), named), (["go.mod"], ["README.md"]))
        p = D.first_prompt(spec, [], shown, named, "Go")
        self.assertTrue(p.endswith("\n\nReply with the complete contents of main.go and any other .go file the program needs, and no other file."), p[-160:])
        self.assertEqual(D.wanted([], "Rust"), "every .rs file the program needs")

class TestReports(unittest.TestCase):
    def test_a_trial_where_nothing_built_says_so(self):
        from codebot.capbench import reports as RP
        self.assertEqual((RP._examples({"delivered_examples": None, "examples_total": 15}), RP._examples({"delivered_examples": 12, "examples_total": 15}), RP._examples(None)),
                         ("nothing built", "12/15", None))

class Proj(Sandboxed):
    """A drafts project over the throwaway code dir, a scripted fake model and no sandbox."""
    spec = SPEC
    def setUp(self):
        super().setUp()
        self.saved = {k: os.environ.get(k) for k in ("OLLAMA_PORT", "SANDBOX", "THERMAL_GUARD")}
        self.fake = FakeOllama([]); os.environ.update(OLLAMA_PORT=str(self.fake.port), SANDBOX="none", THERMAL_GUARD="0")
        with open(os.path.join(self.state, "SPEC.md"), "w") as f: f.write(self.spec)
        self.set_env(WORKFLOW="drafts")
    def tearDown(self):
        self.fake.stop()
        for k, v in self.saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        super().tearDown()
    def run_until_done(self, n=20):
        evs = []
        for _ in range(n):
            evs.append(D.step(self.proj()))
            if evs[-1]["event"] != "round": return evs
        return evs
    def log(self): return subprocess.run(["git", "-C", self.code, "log", "--format=%s"], capture_output=True, text=True).stdout.splitlines()
    def user_prompts(self): return [r["messages"][1]["content"] for r in self.fake.requests]

class TestRounds(Proj):
    def test_a_stalled_draft_restarts_and_the_best_version_is_delivered_and_committed(self):
        self.set_env(DRAFTS_PATIENCE="2"); self.fake.script = [HALF, HALF, HALF, GOOD]
        evs = self.run_until_done()
        self.assertEqual([e["event"] for e in evs], ["round", "round", "round", "all_done"])
        self.assertEqual(evs[-1], {"event": "all_done", "stop": "examples pass", "delivered_round": 4, "examples": "2/2", "declared": "2/2", "rounds": 4, "drafts": 2, "minutes": evs[-1]["minutes"]})
        p = self.user_prompts(); first = D.first_prompt(SPEC, ["mathx.py"], {}, ["README.md"])
        self.assertEqual((p[0], p[3]), (first, first))                                            # a fresh draft starts from the spec alone
        self.assertIn("FAIL: double(-3) returned -1, expected -6", p[1]); self.assertIn("1 of 2 examples from the specification fail", p[1])
        rq = self.fake.requests
        self.assertEqual([r["options"]["seed"] for r in rq], [1001, 1002, 1003, 1004])
        self.assertEqual((rq[0]["options"]["temperature"], rq[0]["options"]["num_predict"], rq[0]["think"], rq[0]["messages"][0]["content"]), (0.2, 6000, False, D.system_prompt("Python")))
        with open(os.path.join(self.code, "mathx.py")) as f: self.assertIn("return 2 * n", f.read())
        self.assertEqual(self.log()[:2], ["drafts: round 4 (draft 2) builds, 2 of 2 spec examples pass, 2 of 2 declared names defined",
                                          "drafts: round 1 (draft 1) builds, 1 of 2 spec examples pass, 1 of 2 declared names defined"])
        s = D.summary(self.proj())
        self.assertEqual((s["rounds"], s["drafts"], s["repairs"], s["unchanged"], s["delivered_round"], s["uncovered"]), (4, 2, 2, 2, 4, [2]))
        rep = D.report(self.proj()); self.assertIn("**2 of 2** spec examples pass", rep); self.assertIn("requirement(s) 2 of SPEC.md have no examples", rep)
        self.assertEqual(D.step(self.proj())["event"], "all_done"); self.assertEqual(len(self.fake.requests), 4)    # a finished run stays finished
    def test_a_worse_later_draft_never_replaces_the_delivered_version(self):
        self.set_env(DRAFTS_MAX_ROUNDS="5", DRAFTS_PATIENCE="2"); self.fake.script = [HALF, BROKEN, BROKEN, BROKEN, BROKEN]
        evs = self.run_until_done()
        self.assertEqual((evs[-1]["event"], evs[-1]["stop"], evs[-1]["delivered_round"], evs[-1]["examples"]), ("finished_incomplete", "round cap", 1, "1/2"))
        with open(os.path.join(self.code, "mathx.py")) as f: self.assertIn("return n + 2", f.read())
        p = self.user_prompts()
        self.assertTrue(all("The code does not compile" not in x for x in p[:4]))           # a draft continues from its best version, not from a worse reply
        self.assertEqual(p[3], D.first_prompt(SPEC, ["mathx.py"], {}, ["README.md"])); self.assertIn("The code does not compile", p[4])
        self.assertIn("still gets wrong", D.report(self.proj())); self.assertIn("FAIL: double(-3)", D.report(self.proj()))
    def test_one_repair_that_does_not_improve_starts_a_fresh_draft_by_default(self):
        self.fake.script = [HALF, HALF, GOOD]
        evs = self.run_until_done(); p = self.user_prompts()
        self.assertEqual([e["event"] for e in evs], ["round", "round", "all_done"]); self.assertEqual(evs[1]["restart_next"], True)
        self.assertEqual(p[2], D.first_prompt(SPEC, ["mathx.py"], {}, ["README.md"])); self.assertEqual(evs[-1]["drafts"], 2)
    def test_a_declared_name_must_exist_even_when_every_example_passes(self):
        only_double = "### mathx.py\n```python\ndef double(n):\n    return 2 * n\n```\n"
        self.fake.script = [only_double, GOOD]
        evs = self.run_until_done(); p = self.user_prompts()
        self.assertEqual([e["event"] for e in evs], ["round", "all_done"])
        self.assertIn("All files compile and all 2 examples from the specification pass, but these names the specification declares are not defined: `half` (mathx.py).", p[1])
        self.assertEqual(D.load(self.proj())["declared"], {"double": "mathx.py", "half": "mathx.py"})
    def test_names_are_read_from_the_syntax_tree_and_builtins_never_count(self):
        path = os.path.join(self.tmp, "m.py")
        with open(path, "w") as f: f.write("import os, json as j\nfrom x import y as z\ntry:\n    def a(): pass\nexcept ImportError:\n    def a(): pass\nif True:\n    class B: pass\nC = D = 1\ndef outer():\n    def inner(): pass\n")
        self.assertEqual(D.defined(path), {"os", "j", "z", "a", "B", "C", "D", "outer"})
        with open(path, "w") as f: f.write("def broken(:\n")
        self.assertIsNone(D.defined(path))
        spec = "Build `g.py`.\n\n1. `run(x)` returns `str(x)`; it raises `BadInput` (a `ValueError`) and `GraphError`."
        self.assertEqual(D.declared(spec, ["g.py"]), {"GraphError": "g.py", "run": "g.py"})
        self.assertEqual(D.declared(spec, []), {}); self.assertEqual(D.declared(spec, ["main.go"]), {})
    def test_a_repair_reply_is_capped_once_a_complete_reply_has_built(self):
        self.set_env(DRAFTS_MAX_ROUNDS="3", DRAFTS_PATIENCE="3"); self.fake.script = [("I am thinking", "length"), HALF, HALF]
        self.run_until_done()
        self.assertEqual([r["options"]["num_predict"] for r in self.fake.requests], [6000, 6000, 3000])     # a cut-off reply sets no cap; the fake's replies are 20 tokens
        self.assertEqual(D.output_cap({"max_complete": 2400}, {"num_predict": 6000}), 6000); self.assertEqual(D.output_cap({"max_complete": 1200}, {"num_predict": 6000}), 3600)
    def test_equal_scores_deliver_the_latest(self):
        self.set_env(DRAFTS_MAX_ROUNDS="2", DRAFTS_PATIENCE="2"); self.fake.script = [HALF, HALF.replace("n + 2", "2 + n")]
        self.run_until_done()
        with open(os.path.join(self.code, "mathx.py")) as f: self.assertIn("2 + n", f.read())
    def test_feedback_for_no_code_missing_files_and_a_cut_off_reply(self):
        self.spec = SPEC.replace("2. `mathx.py` provides `half(n)`", "2. `other.py` provides `half(n)`")
        with open(os.path.join(self.state, "SPEC.md"), "w") as f: f.write(self.spec)
        self.set_env(DRAFTS_MAX_ROUNDS="3"); self.fake.script = ["I cannot.", (HALF, "length"), (HALF, "stop")]
        self.run_until_done(); p = self.user_prompts()
        self.assertIn("did not contain any file as a line `### <file name>` followed by a ```python code block", p[1])
        self.assertIn("Your last reply was cut off because it was too long.\n\nThese files are missing: other.py.", p[2])
        self.assertIn("Reply with the complete contents of mathx.py and other.py, and no other file.", p[0])
    def test_a_reply_that_repeats_itself_is_stopped_and_judged_on_its_last_complete_block(self):
        cycle = "".join(f"# step {i}: think about the order again\n" for i in range(12))
        looping = GOOD + "Wait, let me redo it.\n### mathx.py\n```python\n" + cycle * 30
        self.set_env(DRAFTS_MAX_ROUNDS="2"); self.fake.script = ["### mathx.py\n```python\n" + cycle * 30, looping]
        evs = self.run_until_done(); rs = D.rounds(self.proj())
        self.assertEqual([(r["done_reason"], r["built"], r["examples"]) for r in rs], [("loop", True, 0), ("loop", True, 2)])   # round 2: the complete block before the loop
        self.assertEqual(evs[-1]["event"], "all_done"); self.assertEqual(D.summary(self.proj())["looped"], 2)
        self.assertIn("Your last reply was stopped because it repeated the same lines over and over.\n\nAll files compile, but 2 of 2", self.user_prompts()[1])
        self.assertLess(len(read(D.dpath(self.proj(), "rounds", "02", "reply.md"))), len(looping) // 4)
        self.assertEqual([q["stream"] for q in self.fake.requests], [True, True])
    def test_a_streamed_reply_ends_where_stop_if_says(self):
        self.fake.script = ["a\nb\nc\nd\n", "a\nb\n"]; msgs = [{"role": "user", "content": "x"}]
        r = ollama.chat(self.proj(), msgs, stop_if=lambda t: "enough" if t.count("\n") >= 2 else None)
        self.assertEqual((r["content"], r["done_reason"], r["gen_tokens"]), ("a\nb\n", "enough", 2))
        r = ollama.chat(self.proj(), msgs, stop_if=lambda t: None)
        self.assertEqual((r["content"], r["done_reason"], r["gen_tokens"], r["prompt_tokens"]), ("a\nb\n", "stop", 20, 100))
        self.fake.script = ["whole"]; self.assertEqual(ollama.chat(self.proj(), msgs)["content"], "whole"); self.assertFalse(self.fake.requests[-1]["stream"])
    def test_the_time_cap_counts_only_rounds_and_settings_apply_at_once(self):
        self.set_env(DRAFTS_MINUTES="0"); self.fake.script = [HALF, HALF]
        evs = self.run_until_done()
        self.assertEqual([e["event"] for e in evs], ["round", "finished_incomplete"]); self.assertEqual(evs[-1]["stop"], "time cap")
    def test_a_model_error_does_not_count_a_round(self):
        def boom(*a, **k): raise ollama.ModelError("cannot reach the local model server")
        real = D.ollama.chat; D.ollama.chat = boom
        try: ev = D.step(self.proj())
        finally: D.ollama.chat = real
        self.assertEqual((ev["event"], ev["n"]), ("model_error", 1)); self.assertEqual(D.load(self.proj())["round"], 0)
        self.fake.script = [GOOD]; self.assertEqual(self.run_until_done()[-1]["event"], "all_done")
    def test_a_later_best_without_a_file_takes_it_back_to_the_baseline(self):
        p = self.proj(); st = D.load(p) or D.init(p); self.write_code("keep.py", "x = 1\n")
        D.deliver(p, st, {"mathx.py": "A\n", "README.md": "new\n", "extra.py": "B\n"}, 1, 1, 0)
        D.deliver(p, st, {"mathx.py": "C\n"}, 2, 2, 0)
        self.assertFalse(os.path.exists(os.path.join(self.code, "extra.py")))
        with open(os.path.join(self.code, "README.md")) as f: self.assertEqual(f.read(), "base\n")
        self.assertTrue(os.path.exists(os.path.join(self.code, "keep.py")))                        # never touches a file it did not write
    def test_a_hanging_example_is_redone_one_at_a_time(self):
        self.set_env(DRAFTS_MAX_ROUNDS="1"); self.fake.script = ["### mathx.py\n```python\ndef double(n):\n    while n < 0: pass\n    return 2 * n\n\ndef half(n):\n    return n / 2\n```"]
        real = D.run_examples
        def short(proj, ws, checks, env=None):                       # the real timeouts are 120 s and 20 s; the same logic with 2 s and 1 s
            orig = D.sandbox.execute
            D.sandbox.execute = lambda q, argv, **k: orig(q, argv, **{**k, "timeout": 2 if k["timeout"] == 120 else 1})
            try: return real(proj, ws, checks, env)
            finally: D.sandbox.execute = orig
        D.run_examples = short
        try: self.run_until_done()
        finally: D.run_examples = real
        self.assertEqual(D.load(self.proj())["best"]["feedback"].splitlines()[1:], ["FAIL: double(-3) did not finish within 20 seconds", "1 of 2 assertions failed"])

class TestLoopWatch(unittest.TestCase):
    def feed(self, text):
        w = D.loop_watch()
        for i, c in enumerate(text):
            if c == "\n" and w(text[:i + 1]): return i + 1
    def test_a_repeating_run_of_lines_is_stopped_and_ordinary_code_is_not(self):
        cycle = "".join(f"// idea {i}\n" for i in range(12)); head = "package main\n"
        at = self.feed(head + cycle * 5); self.assertIsNotNone(at); self.assertLessEqual(at, len(head + cycle * 3))
        self.assertIsNone(self.feed("".join(f"if err := step{i}(); err != nil {{\n\treturn err\n}}\n" for i in range(40))))   # a short pattern, never the same 8 lines
        prog = "".join(f"x{i} = {i}\n" for i in range(20))
        self.assertIsNone(self.feed(prog + "Better:\n\n" + prog))        # a file written twice is no loop
        self.assertIsNotNone(self.feed(prog * 3))                         # a third copy is

class TestJudgingParity(Proj):
    """evaluate() gives bare_py.evaluate()'s verdict and feedback, word for word, on level_01."""
    spec = read(os.path.join(CAT, "level_01", "SPEC.md"))
    def test_same_verdicts_and_feedback_as_the_measured_loop(self):
        import bare_py as P
        shutil.copyfile(os.path.join(CAT, "level_01", "seed", "README.md"), os.path.join(self.code, "README.md"))
        p = self.proj(); st = D.init(p); L = P.load("level_01"); sol = os.path.join(CAT, "level_01", "reference_solution")
        good = {n: read(os.path.join(sol, n)) for n in L["files"]}
        versions = [good, {**good, "textutil.py": good["textutil.py"] + "\ndef broken(:\n"}, {k: v for k, v in good.items() if k != "fileutil.py"},
                    {**good, "fileutil.py": good["fileutil.py"].replace("def safe_filename(", "def _old_safe_filename(") + "\ndef safe_filename(name, max_len=255):\n    return name\n"}]
        for v in versions:
            mine, theirs = D.evaluate(p, st, v), P.evaluate(L, os.path.join(self.tmp, "bare"), v)
            self.assertEqual((mine[0], mine[1], mine[3]), theirs)
        self.assertEqual(D.evaluate(p, st, good), (True, 33, 8, ""))

class TestCoverage(unittest.TestCase):
    def spec(self, lv): return read(os.path.join(CAT, lv, "SPEC.md"))
    def test_requirements_without_examples_are_listed(self):
        c = D.coverage(self.spec("level_03")); self.assertEqual((c["examples"], c["uncovered"], c["files"]), (6, [1, 3, 6, 7, 8], ["depgraph.py"]))
        self.assertIn("Nothing will check requirements 1, 3, 6, 7, 8", D.coverage_text(c))
        g = D.coverage(self.spec("calc_go")); self.assertEqual((g["language"], g["build"], g["examples"], g["files"]), ("Go", "go build -o calc .", 15, []))
        z = D.coverage(self.spec("level_02")); self.assertIn("No examples at all", D.coverage_text(z))
        one = D.coverage("Write `x.py`. `x.py` provides `f(a)`: `f(1)` is `2`."); self.assertEqual((one["numbered"], one["examples"]), (False, 1))
    def test_large_code_is_refused(self):
        import tempfile; d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "big.py"), "w") as f: f.write("x = 1\n" * 3000)
        with open(os.path.join(d, "big2.py"), "w") as f: f.write("y = 2\n" * 3000)
        self.assertIsNone(D.fits(None, "spec", d))
        for i in range(12):
            with open(os.path.join(d, f"m{i}.py"), "w") as f: f.write("z = 3\n" * 1000)
        self.assertIn("works up to 60 KB", D.fits(None, "spec", d))

class TestSupervisor(Sandboxed):
    """codebot new / check-spec / start / status / report through the real bash CLI and supervisor, with the fake model and a fake ntfy."""
    def setUp(self):
        super().setUp()
        from test_g_loop import FakeNtfy
        for d in ("bin", "lib", "modes", "memory", "config"):
            shutil.copytree(os.path.join(REPO, d), os.path.join(self.home, d), dirs_exist_ok=True, ignore=shutil.ignore_patterns("local.env", "__pycache__", "projects", "*.snap"))
        self.ntfy, self.fake = FakeNtfy(), FakeOllama([GOOD])
        with open(os.path.join(self.home, "config", "codebot.env"), "w") as f:
            f.write(f'NTFY_SERVER="http://127.0.0.1:{self.ntfy.port}"\nNTFY_TOPIC="t"\nNTFY_ENABLED=1\nPHONE_CONTROL=0\nITER_TIMEOUT=60\nOLLAMA_PORT={self.fake.port}\n'
                    'CODEBOT_MODEL="x"\nSANDBOX=none\nTHERMAL_GUARD=0\nMAX_ITERATIONS=300\nLESSONS_MAX=4000\nTHINK=off\n')
        self.specfile = os.path.join(self.tmp, "SPEC.md")
        with open(self.specfile, "w") as f: f.write(SPEC)
        shutil.rmtree(self.state)
    def tearDown(self): self.fake.stop(); self.ntfy.stop(); super().tearDown()
    def cli(self, *a):
        env = {k: v for k, v in os.environ.items() if k not in ("OLLAMA_PORT", "SANDBOX", "THERMAL_GUARD", "INVOCATION_ID", "WORKFLOW")}
        return subprocess.run([os.path.join(self.home, "bin", "codebot"), *a], capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env)
    def test_new_start_finish_and_review(self):
        r = self.cli("check-spec", self.specfile); self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("Nothing will check requirement 2", r.stdout)
        r = self.cli("new", self.name, self.specfile, self.code); self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn(f"start it: codebot start {self.name}", r.stdout)
        self.assertNotEqual(self.cli("new", self.name, self.specfile, self.code).returncode, 0)                         # never over an existing project
        self.assertIn("outside codebot's own folder", self.cli("new", "other", self.specfile, self.home).stderr)
        r = self.cli("start", self.name); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        end = time.time() + 60
        while time.time() < end and os.path.exists(os.path.join(self.state, ".active")): time.sleep(0.5)
        titles = [m["title"] for m in self.ntfy.msgs]; self.assertIn(f"codebot: {self.name} COMPLETE", titles, read(os.path.join(self.state, "loop.log"))[-3000:])
        done = [m for m in self.ntfy.msgs if "COMPLETE" in m["title"]][0]["body"]; self.assertIn("2/2 spec examples pass, 2/2 declared names defined (round 1)", done); self.assertIn("requirement(s) 2 have no examples", done)
        wt = os.path.join(self.home, "workspaces", self.name)                                           # the code dir has history: the run worked in a worktree on codebot/<name>
        log = subprocess.run(["git", "-C", self.code, "log", "--format=%s", f"codebot/{self.name}"], capture_output=True, text=True).stdout
        self.assertIn("drafts: round 1 (draft 1) builds, 2 of 2 spec examples pass", log); self.assertTrue(os.path.isfile(os.path.join(wt, "mathx.py")))
        self.assertFalse(os.path.exists(os.path.join(self.code, "mathx.py")))                         # your checkout is untouched
        self.assertIn("drafts: finished (examples pass)", self.cli("status", self.name).stdout)
        r = self.cli("start", self.name); self.assertNotEqual(r.returncode, 0); self.assertIn("has finished", r.stderr)
        self.assertIn("**2 of 2** spec examples pass", self.cli("report", self.name).stdout)

class TestBenchmark(CapCase):
    """`codebot bench run --mode drafts`: no planner, no tasks; the trial is scored by the hidden tests like any other."""
    def setUp(self):
        super().setUp()                                       # the capbench test home: the real catalog, no sandbox, no thermal guard
        self.fake = FakeOllama(lambda msgs, body: module_reply(os.path.join(CAT, "level_01", "reference_solution"), ["fileutil.py", "netutil.py", "numutil.py", "textutil.py"]))
        self.saved["OLLAMA_PORT"] = os.environ.get("OLLAMA_PORT"); os.environ["OLLAMA_PORT"] = str(self.fake.port)
    def tearDown(self): self.fake.stop(); super().tearDown()
    def test_drafts_trials_are_scored_by_the_hidden_tests_and_repeats_differ_by_seed(self):
        from codebot.capbench import reports, runner as RN
        store, ctx = self.make(mode="drafts", repeats=2, task_minutes=45)
        self.assertEqual(RN.execute(ctx), "finished")
        tds = store.all_trials()
        for td in tds:
            self.assertEqual((td["outcome"], td["reference"]["passed"], td["reference"]["total"], td["drafts"]["delivered_examples"], td["plan"]["mode"]), ("COMPLETE", 64, 64, 33, "drafts"))
            self.assertEqual(td["plan"]["coverage"]["uncovered"], [7])
        self.assertEqual(sorted(r["options"]["seed"] for r in self.fake.requests), [1001, 2001])
        env = read(os.path.join(self.home, "projects", tds[0]["project"], "project.env"))
        self.assertIn('DRAFTS_MINUTES="45"', env); self.assertIn('WORKFLOW="drafts"', env)
        rows = reports.build(store)["trials"]; self.assertEqual([r["examples"] for r in rows], ["33/33", "33/33"])
        md = reports.markdown(reports.build(store)); self.assertIn("| trial | outcome | spec examples | rounds | drafts | independent |", md); self.assertIn("time per run **45 min**", md)
        self.assertNotIn("## Tasks", md); self.assertIn("spec examples 33/33, 33/33, independent pass rate 100%", reports.one_line(store))
    def test_a_gap_between_the_examples_and_the_hidden_tests_is_classified(self):
        from codebot.capbench import classify as CL
        t = {"plan": {"mode": "drafts", "ok": True}, "drafts": {"delivered_round": 3, "delivered_examples": 6, "examples_total": 6, "complete": True, "uncovered": [8]},
             "reference": {"pass_rate": 0.7, "by_requirement": {"R8": {"passed": 0, "total": 12}}, "failing": ["t1"]}, "tasks": []}
        v = CL.classify_project(t, [{"id": "R8", "title": "subset"}])
        self.assertEqual([x["category"] for x in v], ["VERIFICATION_GAP"]); self.assertEqual(v[0]["evidence"]["uncovered_spec_items"], [8])

if __name__ == "__main__": unittest.main()
