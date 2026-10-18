"""Context setup verification: explicit num_ctx, keep_alive, grammar-constrained schema, window never exceeded, stable cached prefix."""
import json, os, re, subprocess, sys, unittest
from helpers import Sandboxed
from fake_ollama import FakeOllama, A
from codebot import engine, worker, packer, specdoc, tokens, thermal, ollama, tasks as T, audit

REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
PLAN = """
# Goal
Tiny adder.
# Definition of done
a.py has add.
# Tasks
- [ ] 1. write add in a.py
  - check: `python3 -c "import a; assert a.add(1,2)==3, 'add is wrong'"`
  - files: a.py
"""
def kv(path):
    out = {}
    for ln in open(path):
        m = re.match(r"^([A-Z_]+)=(\S+)", ln.strip())
        if m: out[m.group(1)] = m.group(2).strip('"')
    return out

class TestConfigConsistency(unittest.TestCase):
    def test_num_ctx_is_explicit_and_identical_everywhere(self):
        modelfile = open(os.path.join(REPO, "config", "Modelfile")).read()
        mf = int(re.search(r"PARAMETER num_ctx (\d+)", modelfile).group(1))
        env = int(kv(os.path.join(REPO, "config", "codebot.env"))["NUM_CTX"])
        oc = [json.load(open(os.path.join(REPO, "config", f)))["provider"]["ollama"]["models"]["codebot-model"]["limit"]["context"] for f in ("opencode.json", "opencode-re.json")]
        self.assertEqual({mf, env, *oc}, {32768})
    def test_keep_alive_is_configured(self):
        self.assertRegex(kv(os.path.join(REPO, "config", "codebot.env"))["KEEP_ALIVE"], r"^\d+[mh]$")

class Base(Sandboxed):
    def setUp(self):
        super().setUp(); self.write_plan(PLAN); engine.freeze_project(self.proj()); self.fake = None
        os.makedirs(os.path.join(self.home, "logs"), exist_ok=True)
    def tearDown(self):
        if self.fake: self.fake.stop()
        super().tearDown()
    def run_script(self, script, **env):
        self.fake = FakeOllama(script); self.set_env(WORKER="native", OLLAMA_PORT=self.fake.port, **env)
        p = self.proj(); task = T.TaskStore(p).load()["tasks"][0]; return worker.run_attempt(p, task, 1, "AssertionError: add is wrong")
    def calibrate(self, cpt):
        with open(os.path.join(self.home, "logs", "tokenizer_ratio.json"), "w") as f: json.dump({"chars_per_token": cpt}, f)

class TestEveryRequest(Base):
    def test_num_ctx_keep_alive_think_and_schema_on_every_request(self):
        self.run_script([A("list_dir", path="."), A("read_file", path="README.md"), A("finish", summary="s")], NUM_CTX="32768", KEEP_ALIVE="30m")
        self.assertEqual(len(self.fake.requests), 3)
        for r in self.fake.requests:
            self.assertEqual(r["options"]["num_ctx"], 32768); self.assertEqual(r["keep_alive"], "30m"); self.assertFalse(r["think"])
            self.assertIsInstance(r["format"], dict); self.assertIn("anyOf", r["format"])          # grammar-constrained decoding, not just described in the prompt
            self.assertEqual({a["properties"]["action"]["enum"][0] for a in r["format"]["anyOf"]}, set(worker.ACTIONS))
    def test_the_prompt_does_not_merely_describe_the_schema_it_relies_on_format(self):
        self.run_script([A("finish", summary="s")]); self.assertNotIn("anyOf", self.fake.requests[0]["messages"][0]["content"])

class TestWindowNeverExceeded(Base):
    def test_dense_tokenizer_with_a_small_window(self):
        self.calibrate(1.6); self.write_code("big.txt", "\n".join("x = " + "1234567890" * 6 for _ in range(500)))
        self.run_script([A("read_file", path="big.txt", offset=i * 60 + 1, limit=60) for i in range(18)] + [A("finish", summary="s")],
                        NUM_CTX="6000", WORKER_MAX_TOKENS="300", WORKER_MAX_TURNS="19", WORKER_CONTEXT_TOKENS="9999999", CONTEXT_BUDGET="1500")
        true_tokens = lambda r: sum(len(m["content"]) for m in r["messages"]) / 1.6 + 4 * len(r["messages"])          # the dense "real" tokenizer
        self.assertGreater(len(self.fake.requests), 15)
        for r in self.fake.requests: self.assertLessEqual(true_tokens(r) + 300, 6000, "request + generation reserve exceeded the window")
        self.assertTrue(any(r["messages"][2]["content"].startswith("## EARLIER STEPS") for r in self.fake.requests[5:]))     # it compacted to stay inside

    def test_a_context_bigger_than_the_window_is_truncated_not_sent_oversize(self):
        self.calibrate(1.6); self.write_code("a.py", "def add(a, b):\n    return a + b\n" * 400)
        self.run_script([A("finish", summary="s")], NUM_CTX="3000", WORKER_MAX_TOKENS="200", CONTEXT_BUDGET="20000")
        r = self.fake.requests[0]; self.assertLessEqual(sum(len(m["content"]) for m in r["messages"]) / 1.6 + 8 + 200, 3000)
        self.assertIn("context truncated", r["messages"][1]["content"])
        ends = [json.loads(l) for l in open(os.path.join(self.state, "trace.jsonl")) if '"attempt_end"' in l]; self.assertEqual(ends[-1]["truncated"], 1)

    def test_calibration_file_is_conservative_and_clamped(self):
        p = self.proj(); self.assertEqual(tokens.ratio(p), tokens.DEFAULT_RATIO)
        self.calibrate(3.4); self.assertAlmostEqual(tokens.ratio(p), 3.4 * 0.92, places=2)
        self.calibrate(0.5); self.assertEqual(tokens.ratio(p), 1.5); self.calibrate(99); self.assertLessEqual(tokens.ratio(p), 4.0)

class TestCachedPrefix(Base):
    def test_prefix_is_byte_identical_across_turns_even_after_compaction(self):
        self.write_code("big.txt", "\n".join("y" * 90 for _ in range(400)))
        self.run_script([A("read_file", path="big.txt", offset=i * 30 + 1, limit=30) for i in range(16)] + [A("finish", summary="s")],
                        WORKER_CONTEXT_TOKENS="1800", WORKER_MAX_TURNS="17", CONTEXT_BUDGET="1200")
        pre = lambda r: json.dumps(r["messages"][:2], sort_keys=True)
        first = pre(self.fake.requests[0]); self.assertGreater(len(self.fake.requests), 10)
        for i, r in enumerate(self.fake.requests): self.assertEqual(pre(r), first, f"prefix changed at turn {i + 1}")
        self.assertTrue(any(len(r["messages"]) > 2 and r["messages"][2]["content"].startswith("## EARLIER STEPS") for r in self.fake.requests))        # and compaction did happen

    def test_each_turn_extends_the_previous_conversation(self):
        self.run_script([A("list_dir", path="."), A("read_file", path="README.md"), A("list_dir", path="x"), A("finish", summary="s")])
        for prev, cur in zip(self.fake.requests, self.fake.requests[1:]): self.assertEqual(cur["messages"][:len(prev["messages"])], prev["messages"])    # append-only: cache-friendly

    def test_static_sections_come_before_volatile_ones(self):
        task = T.TaskStore(self.proj()).load()["tasks"][0]
        with open(os.path.join(self.state, "GRILL.md"), "w") as f: f.write("# Decisions\n- ints only\n")
        text, _ = packer.pack(self.proj(), task, "AssertionError: add is wrong")
        order = [text.index(h) for h in ("## PROJECT GOAL", "## AGREED DECISIONS", "## REPO MAP", "## TASK", "## ACCEPTANCE CHECK", "## LAST CHECK OUTPUT")]
        self.assertEqual(order, sorted(order))

    def test_two_attempts_share_a_long_common_prefix(self):
        task = T.TaskStore(self.proj()).load()["tasks"][0]
        t1, _ = packer.pack(self.proj(), task, "AssertionError: add is wrong"); t2, _ = packer.pack(self.proj(), task, "NameError: name 'add' is not defined")
        common = os.path.commonprefix([t1, t2]); self.assertGreater(len(common), t1.index("## LAST CHECK OUTPUT") - 5)

SPEC = """# Tiny calculator
Use the standard library only. All code is in calc.py.

## Requirements

1. `add(a, b)` returns the sum. UNIQUE-ONE
2. `sub(a, b)` returns the difference: `sub(5, 3)` is `2`. UNIQUE-TWO
3. `mul(a, b)` returns the product. UNIQUE-THREE
"""

class TestSpecInContext(Base):
    """2026-10-17 design review: SPEC.md lives in the state dir, which the worker may not read, so it never saw the spec; its picture of the project
    was the task title, the frozen checks and the code. Now the harness packs the spec's introduction and the task's own requirement."""
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.state, "SPEC.md"), "w") as f: f.write(SPEC)
    def pack(self, task=None, **kw):
        task = task or T.TaskStore(self.proj()).load()["tasks"][0]; return packer.pack(self.proj(), task, "", **kw)
    def test_the_task_sees_the_introduction_and_its_own_requirement_only(self):
        specdoc.save_map(self.proj(), {"T1": 2})
        text, man = self.pack()
        self.assertIn("standard library only", text); self.assertIn("requirement 2 of 3", text); self.assertIn("UNIQUE-TWO", text)
        self.assertNotIn("UNIQUE-ONE", text); self.assertNotIn("UNIQUE-THREE", text)
        self.assertIn("SPECIFICATION", [n for n, _ in man["sections"]])
    def test_a_subtask_and_a_task_added_during_its_root_see_the_roots_requirement(self):
        specdoc.save_map(self.proj(), {"T1": 3})
        for t in ({"id": "T1.2", "title": "part"}, {"id": "T9", "title": "added", "budget_root": "T1"}):
            text, _ = self.pack(t); self.assertIn("UNIQUE-THREE", text, t["id"]); self.assertNotIn("UNIQUE-TWO", text)
    def test_without_a_recorded_requirement_a_short_spec_is_shown_whole(self):
        text, _ = self.pack(); self.assertIn("UNIQUE-ONE", text); self.assertIn("UNIQUE-THREE", text)
    def test_without_a_spec_nothing_changes(self):
        os.remove(os.path.join(self.state, "SPEC.md")); _, man = self.pack(); self.assertNotIn("SPECIFICATION", [n for n, _ in man["sections"]])
    def test_the_spec_comes_after_the_stable_sections_and_before_the_task(self):
        specdoc.save_map(self.proj(), {"T1": 1}); text, _ = self.pack()
        self.assertLess(text.index("## REPO MAP"), text.index("## SPECIFICATION")); self.assertLess(text.index("## SPECIFICATION"), text.index("## TASK"))
    def test_the_spec_survives_a_tight_budget_before_the_repo_map_does(self):
        specdoc.save_map(self.proj(), {"T1": 2}); _, man = self.pack(budget_tokens=500)
        names = [n for n, _ in man["sections"]]; self.assertIn("SPECIFICATION", names)

class TestCheckInContext(Base):
    def test_every_assertion_is_shown_numbered_and_the_runner_source_is_not(self):
        # 2026-10-17 calc_go control rerun, T7: the runner's source and the indented spec shared a 2,400-character cap; the worker saw 1 of 12 assertions
        from codebot import checkspec as K
        K.ensure_runner(self.proj())
        asserts = [{"type": "run", "argv": ["go", "run", ".", "echo", f"{i} + {i} with a long tail of words"], "stdout": f"{2 * i}\n", "exit": 0} for i in range(1, 13)]
        K.write_spec(self.proj(), "T7", asserts); task = {"id": "T7", "title": "stdin", "check": K.command_for("T7")}
        text, _ = packer.pack(self.proj(), task, "")
        for i in range(1, 13): self.assertIn(f"\n{i}. {json.dumps(asserts[i - 1])}", text)
        self.assertNotIn("import importlib", text); self.assertIn(K.command_for("T7"), text)
    def test_the_runner_reports_failing_assertions_by_those_numbers(self):
        from codebot import checkspec as K
        K.ensure_runner(self.proj()); self.write_code("m.py", "def f(): return 1\n")
        K.write_spec(self.proj(), "T1", [{"type": "call", "module": "m", "func": "f", "expect": 1}, {"type": "call", "module": "m", "func": "f", "expect": 2},
                                         {"type": "file_exists", "path": "m.py"}, {"type": "file_exists", "path": "nope.py"}])
        r = subprocess.run([sys.executable, os.path.join(self.state, "checks", K.RUNNER), os.path.join(self.state, "checks", "T1.spec.json")], cwd=self.code, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1); self.assertIn("\nfailing assertions: 2, 4\n", r.stdout)

class TestStatsAndThermal(Base):
    def test_timing_stats_reach_the_trace_and_the_audit_log(self):
        self.run_script([A("list_dir", path="."), A("finish", summary="s")])
        turns = [json.loads(l) for l in open(os.path.join(self.state, "trace.jsonl")) if '"event": "turn"' in l]
        self.assertEqual((turns[0]["prompt_ms"], turns[0]["gen_ms"]), (2000, 1000)); self.assertEqual(turns[0]["kind"], "ok"); self.assertGreater(turns[0]["est_prompt"], 50)
        mr = [e for e in audit.entries(self.proj()) if e["kind"] == "model_request"]; self.assertEqual((mr[0]["prompt_ms"], mr[0]["gen_ms"], mr[0]["total_ms"]), (2000, 1000, 3000))
    def test_malformed_and_action_errors_are_counted_separately(self):
        self.run_script(["not json", A("read_file", path="nope.txt"), A("finish", summary="s")])
        ends = [json.loads(l) for l in open(os.path.join(self.state, "trace.jsonl")) if '"attempt_end"' in l]; self.assertEqual((ends[-1]["malformed"], ends[-1]["action_errors"]), (1, 1))

    def test_thermal_guard_waits_until_cool(self):
        self.set_env(THERMAL_GUARD="1", THERMAL_MAX="90", THERMAL_RESUME="80"); temps = iter([95, 93, 88, 79]); slept = []
        w = thermal.wait(self.proj(), sleep=slept.append, reader=lambda: next(temps)); self.assertEqual((w, slept), (60, [20, 20, 20]))
        ev = [json.loads(l) for l in open(os.path.join(self.state, "thermal.jsonl"))]; self.assertEqual([e["event"] for e in ev], ["pause", "resume"])
    def test_thermal_guard_is_a_noop_when_cool_disabled_or_blind(self):
        slept = []
        self.set_env(THERMAL_GUARD="1"); self.assertEqual(thermal.wait(self.proj(), sleep=slept.append, reader=lambda: 70), 0)
        self.assertEqual(thermal.wait(self.proj(), sleep=slept.append, reader=lambda: None), 0)
        self.set_env(THERMAL_GUARD="0"); self.assertEqual(thermal.wait(self.proj(), sleep=slept.append, reader=lambda: 99), 0); self.assertEqual(slept, [])
    def test_thermal_wait_has_a_cap(self):
        self.set_env(THERMAL_GUARD="1", THERMAL_MAX_WAIT="60"); slept = []
        self.assertEqual(thermal.wait(self.proj(), sleep=slept.append, reader=lambda: 99), 60)
    def test_chat_consults_the_thermal_guard_before_every_request(self):
        calls = []; old = thermal.wait; thermal.wait = lambda proj, **kw: calls.append(1) or 0
        try: self.run_script([A("list_dir", path="."), A("finish", summary="s")])
        finally: thermal.wait = old
        self.assertEqual(len(calls), 2)

if __name__ == "__main__": unittest.main()
