"""Declarative checks: the model emits assertions (data); the harness owns paths, interpreter, exit-status contract and PLAN.md. Covers the runner,
the guard's strict exit-status rule, the native planner (with the scripted fake model, including a repair round) and the splitter."""
import json, os, subprocess, sys, unittest
from helpers import Sandboxed
from fake_ollama import FakeOllama
from codebot import assertions as A, checkspec as K, checks as C, engine, guard, planner, planops, planparse, splitter, tasks as T

RUNNER = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "modes", "code", "verifiers", "assert_runner.py")

class RunnerCase(Sandboxed):
    def run_spec(self, asserts):
        spec = os.path.join(self.tmp, "spec.json")
        with open(spec, "w") as f: json.dump({"asserts": asserts}, f)
        p = subprocess.run([sys.executable, RUNNER, spec], cwd=self.code, capture_output=True, text=True); return p.returncode, p.stdout

class TestRunner(RunnerCase):
    def setUp(self):
        super().setUp(); self.write_code("textutil.py", "def slug(t): return '-'.join(t.lower().split())\ndef boom(): raise ValueError('x')\n")
        self.write_code("cli.py", "import sys, json\nprint(json.dumps({'a': 1, 'b': 2}) if sys.argv[1:] == ['kv'] else 'hello-world')\n")
    def test_all_hold_is_exit_0(self):
        rc, out = self.run_spec([{"type": "call", "module": "textutil", "func": "slug", "args": ["Hello World"], "expect": "hello-world"},
                                 {"type": "call", "module": "textutil", "func": "boom", "raises": "ValueError"},
                                 {"type": "run", "argv": ["python3", "cli.py", "slug"], "stdout": "hello-world"},
                                 {"type": "run", "argv": ["python3", "cli.py", "kv"], "stdout_json": {"b": 2, "a": 1}},
                                 {"type": "file_exists", "path": "cli.py"}, {"type": "file_contains", "path": "cli.py", "text": "json"}, {"type": "file_not_contains", "path": "cli.py", "text": "old_name"}])
        self.assertEqual(rc, 0, out)
    def test_a_failing_assertion_is_exit_1_with_the_reason(self):
        rc, out = self.run_spec([{"type": "call", "module": "textutil", "func": "slug", "args": ["A B"], "expect": "wrong"}, {"type": "call", "module": "textutil", "func": "missing", "expect": 1},
                                 {"type": "call", "module": "nomodule", "func": "f", "expect": 1}, {"type": "file_not_contains", "path": "cli.py", "text": "json"},
                                 {"type": "run", "argv": ["python3", "cli.py", "x"], "stdout": "nope"}, {"type": "file_exists", "path": "nofile.py"}])
        self.assertEqual(rc, 1, out)
        for frag in ("returned 'a-b', expected 'wrong'", "textutil.missing does not exist", "cannot import module nomodule", "still contains", "printed 'hello-world'", "nofile.py does not exist"): self.assertIn(frag, out)
    def test_unusable_specs_are_exit_2_never_a_failing_test(self):
        for bad in ([], [{"type": "shell", "cmd": "ls"}], [{"type": "call", "module": "m", "func": "f"}], [{"type": "file_exists", "path": "/etc/passwd"}], [{"type": "file_exists", "path": "../x"}],
                    [{"type": "run", "argv": ["ls"]}], [{"type": "call", "module": "m", "func": "f", "expect": 1, "bogus": 1}]):
            rc, out = self.run_spec(bad); self.assertEqual(rc, 2, (bad, out)); self.assertIn("SPEC ERROR", out)
        spec = os.path.join(self.tmp, "x.json"); open(spec, "w").write("{not json")
        self.assertEqual(subprocess.run([sys.executable, RUNNER, spec], cwd=self.code, capture_output=True).returncode, 2)
        self.assertEqual(subprocess.run([sys.executable, RUNNER, "/no/such/spec.json"], cwd=self.code, capture_output=True).returncode, 2)
    def test_numbers_compare_like_json_and_bool_is_not_a_number(self):
        self.write_code("n.py", "def f(): return 2\ndef g(): return True\ndef h(): return 0.1 + 0.2\n")
        rc, out = self.run_spec([{"type": "call", "module": "n", "func": "f", "expect": 2.0}, {"type": "call", "module": "n", "func": "h", "expect": 0.3}]); self.assertEqual(rc, 0, out)
        rc, out = self.run_spec([{"type": "call", "module": "n", "func": "g", "expect": 1}]); self.assertEqual(rc, 1, out)
    def test_empty_string_expectation_is_a_real_expectation(self):
        self.write_code("e.py", "def s(t): return t.strip()\n")
        rc, out = self.run_spec([{"type": "call", "module": "e", "func": "s", "args": ["  "], "expect": ""}]); self.assertEqual(rc, 0, out)

class TestCheckspec(Sandboxed):
    def test_schema_variants_match_the_runner_contract(self):
        vs = K.assert_schema()["items"]["anyOf"]; self.assertGreaterEqual(len(vs), 9)
        sample = {"module": "m", "func": "f", "args": [], "expect": 1, "raises": "ValueError", "argv": ["python3", "x.py"], "exit": 0, "stdout": "o", "stdout_contains": ["o"], "stdout_json": {}, "path": "a.py", "text": "t"}
        for v in vs:
            t = v["properties"]["type"]["enum"][0]; req, opt = A.TYPES[t]; keys = set(v["properties"]) - {"type"}
            self.assertLessEqual(keys, req | opt, v); self.assertFalse(v["additionalProperties"])
            self.assertIsNone(A.validate([{"type": t, **{k: sample[k] for k in v["required"] if k != "type"}}]), v["required"])      # the minimal instance of every variant is a valid assertion
        calls = [v for v in vs if v["properties"]["type"]["enum"] == ["call"]]; self.assertTrue(calls and all("args" in v["required"] for v in calls))       # a call can never be generated without its inputs
        runs = [v for v in vs if v["properties"]["type"]["enum"] == ["run"]]
        self.assertTrue(all(set(v["required"]) & {"exit", "stdout", "stdout_contains", "stdout_json"} for v in runs))               # a run can never be generated without an expectation
    def test_clean_keeps_only_the_fields_the_type_takes_and_keeps_empty_values(self):
        a = K.clean({"type": "call", "module": "m", "func": "f", "args": [1], "expect": "", "bogus": 1})
        self.assertEqual(a, {"type": "call", "module": "m", "func": "f", "args": [1], "expect": ""}); self.assertIsNone(A.validate([a]))      # "" is a real expectation
        r = K.clean({"type": "run", "argv": ["python3", "cli.py", "slug", ""], "stdout": ""}); self.assertEqual(r["stdout"], ""); self.assertIsNone(A.validate([r]))
        self.assertNotIn("expect", K.clean({"type": "call", "module": "m", "func": "f", "expect": 1, "raises": "ValueError"}))
    def test_a_self_contradicting_assertion_list_is_unusable(self):
        both = [{"type": "file_contains", "path": "a.py", "text": "def f"}, {"type": "file_not_contains", "path": "a.py", "text": "def f"}]
        self.assertIn("contradiction", A.validate(both))
        self.assertIsNone(A.validate([{"type": "file_contains", "path": "a.py", "text": "def f"}, {"type": "file_not_contains", "path": "b.py", "text": "def f"}]))
    def test_the_planner_prompt_says_assertions_describe_the_finished_state(self):
        for frag in ("FINISHED state", "never assert that something the task creates is absent", "never invent a command-line interface", "never to mean 'does not work yet'"): self.assertIn(frag, planner.GUIDE)
    def test_command_has_no_path_or_shell_syntax(self):
        c = K.command_for("T3"); self.assertEqual(c, "python3 $CODEBOT_CHECKS/assert_runner.py $CODEBOT_CHECKS/T3.spec.json"); self.assertNotIn("`", c)
    def test_rendered_plan_parses_back_with_its_checks(self):
        text = K.render_plan("g", "c", "d", [{"id": 1, "title": "one", "files": ["a.py"], "size": "S"}, {"id": 2, "title": "two", "files": [], "size": "M", "deps": [1]}])
        _, tasks = planparse.parse_plan(text); self.assertEqual([t["id"] for t in tasks], ["T1", "T2"]); self.assertEqual(tasks[1]["deps"], ["T1"])
        self.assertEqual(tasks[0]["check"], K.command_for("T1")); self.assertIsNone(tasks[0].get("check_note")); self.assertEqual(tasks[0]["files"], ["a.py"])

class TestGuardStrict(Sandboxed):
    def setUp(self):
        super().setUp(); self.set_env(CHECK_BASELINE_GUARD="1"); os.makedirs(os.path.join(self.state, "checks")); self.write_code("m.py", "def f(): return 1\n")
        K.ensure_runner(self.proj())
    def verdict(self, asserts, tid="T1"):
        K.write_spec(self.proj(), tid, asserts); return guard.probe(self.proj(), K.command_for(tid), 30)
    def test_holds_now_means_it_tests_nothing(self): self.assertEqual(self.verdict([{"type": "call", "module": "m", "func": "f", "expect": 1}])["verdict"], "passes")
    def test_failing_assertion_is_a_real_test(self): self.assertEqual(self.verdict([{"type": "call", "module": "m", "func": "f", "expect": 2}])["verdict"], "ok")
    def test_a_spec_error_is_broken_not_a_good_failing_check(self):
        r = guard.probe(self.proj(), K.command_for("T9"), 30); self.assertEqual(r["verdict"], "broken"); self.assertIn("does not exist", r["out"])      # no spec file at all
        open(os.path.join(self.state, "checks", "T8.spec.json"), "w").write('{"asserts": [{"type": "shell"}]}')                                      # a spec the runner refuses: exit 2
        r = guard.probe(self.proj(), K.command_for("T8"), 30); self.assertEqual(r["verdict"], "broken"); self.assertIn("SPEC ERROR", r["out"])

PLAN_REPLY = {"goal": "Utilities.", "context": "stdlib only", "done": "all assertions hold", "tasks": [
    {"title": "Add slugify", "files": ["textutil.py"], "size": "S", "deps": [], "asserts": [
        {"type": "call", "module": "textutil", "func": "slugify", "args": ["Hello World"], "expect": "hello-world"}, {"type": "file_exists", "path": "textutil.py"}]},
    {"title": "Keep README", "files": ["README.md"], "size": "S", "deps": [1], "asserts": [{"type": "file_exists", "path": "README.md"}]},     # already holds: the guard rejects it
    {"title": "Bad types", "files": [], "size": "S", "deps": [5], "asserts": [{"type": "shell", "cmd": "ls"}]}]}

class TestPlanner(Sandboxed):
    def start(self, script):
        self.fake = FakeOllama(script); self.addCleanup(self.fake.stop); self.set_env(OLLAMA_PORT=self.fake.port, CHECK_BASELINE_GUARD="1", THERMAL_GUARD="0")
        with open(os.path.join(self.state, "SPEC.md"), "w") as f: f.write("Build slugify.\n")
    def test_plan_then_a_repair_round_for_only_the_rejected_tasks(self):
        fix = {"tasks": [{"id": 2, "asserts": [{"type": "file_exists", "path": "cli.py"}]}, {"id": 3, "asserts": [{"type": "call", "module": "textutil", "func": "title", "args": ["a"], "expect": "A"}]}]}
        self.start([PLAN_REPLY, fix]); info = planner.plan_auto(self.home, self.name, 600, log=lambda *_: None)
        self.assertTrue(info["ok"], info); self.assertEqual(len(info["rounds"]), 2); self.assertEqual(sorted(info["rounds"][0]["rejected"]), ["T2:passes", "T3:malformed"]); self.assertEqual(info["rounds"][1]["rejected"], [])
        self.assertIn("ONLY these tasks", self.fake.requests[1]["messages"][1]["content"]); self.assertIn("already hold", self.fake.requests[1]["messages"][1]["content"])
        _, tasks = planparse.parse_plan(self.read("PLAN.md")); self.assertEqual([t["check"] for t in tasks], [K.command_for(f"T{i}") for i in (1, 2, 3)]); self.assertEqual(tasks[2]["deps"], [])      # the dep on a task that does not exist was dropped
        data = engine.freeze_project(self.proj()); self.assertEqual(C.verify(self.proj(), data), [])       # the spec files and the runner are frozen and locked
        self.assertTrue(os.path.exists(os.path.join(self.state, "checks", "T1.spec.json"))); self.assertTrue(os.path.exists(os.path.join(self.state, "checks", "assert_runner.py")))
        self.assertNotIn(self.state, json.dumps(json.load(open(os.path.join(self.state, "checks", "T1.spec.json")))))            # no absolute path anywhere the model can influence
    def test_invalid_json_reply_is_retried_not_fatal(self):
        good = {**PLAN_REPLY, "tasks": PLAN_REPLY["tasks"][:1]}; self.start(["not json at all", good])
        info = planner.plan_auto(self.home, self.name, 600, log=lambda *_: None); self.assertTrue(info["ok"], info); self.assertEqual(info["rounds"][0]["ok"], False)
        self.assertIn("not valid JSON", self.fake.requests[1]["messages"][1]["content"])
    def test_no_spec_means_no_plan(self):
        self.fake = FakeOllama([]); self.addCleanup(self.fake.stop); self.set_env(OLLAMA_PORT=self.fake.port)
        self.assertEqual(planner.plan_auto(self.home, self.name, 60, log=lambda *_: None)["rounds"], [])

class TestSplitter(Sandboxed):
    def setUp(self):
        super().setUp(); self.write_plan("# Tasks\n- [ ] 1. make textutil\n  - check: `python3 -c \"import textutil\"`\n  - files: textutil.py\n"); self.set_env(CHECK_BASELINE_GUARD="1")
        engine.freeze_project(self.proj())
    SUBS = [{"title": "slugify", "files": [], "asserts": [{"type": "call", "module": "textutil", "func": "slugify", "args": ["A B"], "expect": "a-b"}]},
            {"title": "parse", "files": ["x.py"], "asserts": [{"type": "call", "module": "textutil", "func": "parse_kv", "args": ["a=1"], "expect": {"a": 1}}]}]
    def test_build_makes_spec_fixtures_and_runner_commands_and_inherits_files(self):
        K.ensure_runner(self.proj()); kids, fx = splitter.build(self.proj(), "T1", self.SUBS, ["textutil.py"])
        self.assertEqual([k["check"] for k in kids], [K.command_for("T1.1"), K.command_for("T1.2")]); self.assertEqual([k["files"] for k in kids], [["textutil.py"], ["x.py"]])
        self.assertEqual(sorted(fx), ["T1.1.spec.json", "T1.2.spec.json"])
    def test_runner_is_supplied_when_the_project_predates_declarative_checks(self):
        os.remove(os.path.join(self.state, "checks", K.RUNNER)) if os.path.exists(os.path.join(self.state, "checks", K.RUNNER)) else None
        _, fx = splitter.build(self.proj(), "T1", self.SUBS); self.assertIn(K.RUNNER, fx)
    def test_children_with_unusable_assertions_are_dropped(self):
        kids, _ = splitter.build(self.proj(), "T1", [self.SUBS[0], {"title": "bad", "asserts": [{"type": "nope"}]}, {"title": "", "asserts": self.SUBS[0]["asserts"]}]); self.assertEqual(len(kids), 1)
    def test_split_through_planops_is_guarded_and_locked(self):
        kids, fx = splitter.build(self.proj(), "T1", self.SUBS, ["textutil.py"]); self.assertIn(K.RUNNER, fx)       # this project has no runner yet: it ships as a locked fixture
        r = planops.apply(self.proj(), {"op": "split_task", "task": "T1", "subtasks": kids, "fixtures": fx, "reason": "r"}, "harness"); self.assertTrue(r["ok"], r)
        self.assertEqual(r["result"], ["T1.1", "T1.2"]); self.assertEqual(C.verify(self.proj(), T.TaskStore(self.proj()).load()), [])
    def test_a_child_whose_assertions_already_hold_rejects_the_split(self):
        K.ensure_runner(self.proj()); holds = [{"title": "readme", "asserts": [{"type": "file_exists", "path": "README.md"}]}, self.SUBS[1]]
        kids, fx = splitter.build(self.proj(), "T1", holds)
        r = planops.apply(self.proj(), {"op": "split_task", "task": "T1", "subtasks": kids, "fixtures": fx, "reason": "r"}, "harness")
        self.assertFalse(r["ok"]); self.assertIn("subtask 1", r["error"]); self.assertFalse(os.path.exists(os.path.join(self.state, "checks", "T1.1.spec.json")))

class TestAudit(Sandboxed):
    def setUp(self):
        super().setUp(); suite = os.path.join(self.home, "bench", "suite", "s"); os.makedirs(os.path.join(suite, "seed")); os.makedirs(os.path.join(suite, "reference"))
        open(os.path.join(suite, "seed", "a.py"), "w").write("def f(): return 0\n"); open(os.path.join(suite, "reference", "a.py"), "w").write("def f(): return 1\n")
        K.ensure_runner(self.proj())
    def spec(self, tid, asserts): K.write_spec(self.proj(), tid, asserts)
    def test_sound_unsound_and_vacuous_specs_are_told_apart(self):
        self.spec("T1", [{"type": "call", "module": "a", "func": "f", "expect": 1}])      # fails on the seed, holds on the reference: a good check
        self.spec("T2", [{"type": "call", "module": "a", "func": "f", "expect": 2}])      # fails on the reference too: unsound
        self.spec("T3", [{"type": "call", "module": "a", "func": "f", "expect": 0}, {"type": "file_exists", "path": "a.py"}])   # holds on the seed: vacuous (and unsound on the reference)
        r = planner.audit_against_reference(self.home, self.name, "s")
        self.assertEqual((r["tasks"]["T1"]["seed"], r["tasks"]["T1"]["reference"]), (1, 0)); self.assertEqual(r["unsound"], ["T2", "T3"]); self.assertEqual(r["vacuous"], ["T3"])
        self.assertIn("returned 1, expected 2", r["tasks"]["T2"]["why"][0])
    def test_no_reference_means_no_audit(self):
        self.assertIsNone(planner.audit_against_reference(self.home, self.name, "nosuite"))

if __name__ == "__main__": unittest.main()
