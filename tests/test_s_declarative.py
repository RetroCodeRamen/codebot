"""Declarative checks: the model emits assertions (data); the harness owns paths, interpreter, exit-status contract and PLAN.md. Covers the runner,
the guard's strict exit-status rule, the native planner (with the scripted fake model, including a repair round) and the splitter."""
import json, os, subprocess, sys, unittest
from helpers import Sandboxed
from fake_ollama import FakeOllama
from codebot import assertions as A, checkspec as K, checks as C, engine, grounding as G, guard, planner, planops, planparse, rederive as RD, specdoc, splitter, tasks as T

REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

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
    def test_checks_no_code_can_pass_are_unusable(self):
        # verbatim from the 2026-10-15 level-1 run (trial 2): a schema field leaked into the inputs, and a stray ] in python3 -c
        for bad in ([{"type": "call", "module": "textutil", "func": "slugify", "args": ["", "expect"], "expect": ""}],
                    [{"type": "call", "module": "m", "func": "f", "args": [], "kwargs": {"raises": 1}, "expect": 1}],
                    [{"type": "run", "argv": ["python3", "-c", "from textutil import truncate; print(truncate.__doc__)]", "stdout_contains"], "exit": 0}],
                    [{"type": "run", "argv": ["python3", "-c", "print((1)"], "exit": 0}]):
            rc, out = self.run_spec(bad); self.assertEqual(rc, 2, (bad, out)); self.assertIn("SPEC ERROR", out)
        rc, out = self.run_spec([{"type": "call", "module": "textutil", "func": "slug", "args": ["expected Results"], "expect": "expected-results"},     # near-misses stay legal
                                 {"type": "run", "argv": ["python3", "-c", "import textutil; print(textutil.slug('A B'))"], "stdout": "a-b"}])
        self.assertEqual(rc, 0, out)
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
    def test_a_whole_command_in_the_program_name_is_split(self):
        # 2026-10-16 calc_go, control planner: {"argv": ["go run .", "2", "+", "3"]} can never run (no program is called "go run .")
        self.assertEqual(K.clean({"type": "run", "argv": ["go run .", "2", "+", "3"], "stdout": "5"})["argv"], ["go", "run", ".", "2", "+", "3"])
        self.assertEqual(K.clean({"type": "run", "argv": ["./calc", "a b"], "stdout": "x"})["argv"], ["./calc", "a b"])     # arguments may contain spaces
    def test_a_field_name_leaked_into_the_inputs_is_removed(self):
        # every real ornith plan so far: "args": ["", "expect"] whenever the expected value is empty, and repair rounds repeat it
        self.assertEqual(K.clean({"type": "call", "module": "textutil", "func": "slugify", "args": ["", "expect"], "expect": ""})["args"], [""])
        r = K.clean({"type": "run", "argv": ["python3", "-c", "import m; print(m.f())", "stdout_contains"], "exit": 0}); self.assertEqual(r["argv"], ["python3", "-c", "import m; print(m.f())"])
        self.assertIsNone(A.validate([r])); self.assertEqual(K.clean({"type": "call", "module": "m", "func": "f", "args": ["expected"], "expect": 1})["args"], ["expected"])
    def test_repair_plan_uses_the_plan_wide_arity(self):
        c = lambda args, **kw: {"type": "call", "module": "m", "func": "f", "args": args, **kw}
        groups, notes = K.repair_plan([[c(["x"], expect="X"), c(["y"], expect="Y"), c(["z"], expect="Z")], [c(["", ""], expect=""), c(["", False], expect=False), c(["a", "b"], expect="c")]])
        self.assertEqual([a["args"] for a in groups[1]], [[""], [""], ["a", "b"]]); self.assertEqual(len(notes), 2)      # usual arity 1 across the plan; only an extra input EQUAL to the expected value goes
        groups, _ = K.repair_plan([[{**c([[1, 2]], expect=1.5), "func": "mean"}, {**c([1, 2, 3], expect=2.0), "func": "mean"}]])
        self.assertEqual(groups[0][1]["args"], [[1, 2, 3]])
        two = [c(["hello", 8], expect="hello..."), c(["hello", 8, ".."], expect="hell..")]                                              # a real optional argument is never touched
        self.assertEqual(K.repair_plan([two])[0][0], two)
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
    def test_a_program_the_task_has_not_built_yet_is_a_real_failing_check(self):
        # 2026-10-16 calc_go: every check that ran ./calc was rejected as broken ("command not found"), so the plan lost 4 of its 7 tasks
        r = self.verdict([{"type": "run", "argv": ["./calc", "2", "+", "3"], "stdout": "5"}]); self.assertEqual(r["verdict"], "ok", r); self.assertIn("does not exist yet", r["out"])
        r = self.verdict([{"type": "run", "argv": ["bin/calc", "2"], "stdout": "2"}] * 30); self.assertEqual(r["verdict"], "ok", r)
    def test_a_tool_the_sandbox_does_not_have_is_broken(self):
        r = self.verdict([{"type": "run", "argv": ["no-such-tool-xyz", "--version"], "exit": 0}]); self.assertEqual(r["verdict"], "broken"); self.assertIn("not available", r["out"])
        many = [{"type": "run", "argv": ["no-such-tool-xyz"], "exit": 0}] + [{"type": "run", "argv": ["./calc", str(i)], "stdout": str(i)} for i in range(40)]
        self.assertEqual(self.verdict(many)["verdict"], "broken")                            # found even when the output is long and the line is not in its tail
    def test_a_check_no_code_can_pass_is_broken_although_it_fails_on_the_seed(self):
        r = self.verdict([{"type": "call", "module": "m", "func": "f", "args": ["", "expect"], "expect": ""}]); self.assertEqual(r["verdict"], "broken"); self.assertIn("'expect'", r["out"])
        r = self.verdict([{"type": "run", "argv": ["python3", "-c", "import m; print(m.f())]"], "stdout": "2"}]); self.assertEqual(r["verdict"], "broken"); self.assertIn("not valid Python", r["out"])

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
    def test_ungrounded_source_text_is_dropped_but_quoted_spec_text_is_kept(self):
        spec = "1. Rename old_name to double in legacy.py."; keep = {"type": "file_not_contains", "path": "legacy.py", "text": "old_name"}; call = {"type": "call", "module": "m", "func": "f", "expect": 1}
        drop = {"type": "file_contains", "path": "legacy.py", "text": "def double"}
        self.assertEqual(planner.ungrounded([keep, call, drop], spec), ([keep, call], [drop]))
        self.assertEqual(planner.spec_items("1. a\n2) b\n  3. c\nnot 4. item"), 3)
    def test_a_plan_with_fewer_tasks_than_spec_items_is_sent_back(self):
        good = {**PLAN_REPLY, "tasks": PLAN_REPLY["tasks"][:1]}; two = {**PLAN_REPLY, "tasks": [PLAN_REPLY["tasks"][0], {**PLAN_REPLY["tasks"][0], "title": "Also"}]}
        self.start([good, two]); open(os.path.join(self.state, "SPEC.md"), "w").write("1. Add slugify.\n2. Add title.\n"); self.set_env(PLAN_PER_ITEM="0")          # the whole-plan path
        info = planner.plan_auto(self.home, self.name, 600, log=lambda *_: None)
        self.assertEqual(info["rounds"][0]["rc"], 1); self.assertIn("numbered items", self.fake.requests[1]["messages"][1]["content"])
        self.assertIn("numbered items", info["rounds"][0]["error"]); self.assertEqual(info["rounds"][0]["tasks"], None)           # why the round failed is recorded, not only that it did
        log = [json.loads(l) for l in open(os.path.join(self.state, "plan.log")) if "plan_round_failed" in l]; self.assertIn("numbered items", log[0]["reason"])
        self.assertEqual(json.load(open(os.path.join(self.state, "plan.reply.1.json")))["tasks"][0]["title"], "Add slugify")                # and the model's own reply is kept
    def test_the_plan_schema_demands_one_task_per_spec_item(self):
        self.start([{**PLAN_REPLY, "tasks": PLAN_REPLY["tasks"][:1]}, {**PLAN_REPLY, "tasks": PLAN_REPLY["tasks"][:1] * 3}]); self.set_env(PLAN_PER_ITEM="0")          # the whole-plan path
        open(os.path.join(self.state, "SPEC.md"), "w").write("1. a\n2. b\n3. c\n"); planner.plan_auto(self.home, self.name, 600, log=lambda *_: None)
        self.assertEqual(self.fake.requests[0]["format"]["properties"]["tasks"]["minItems"], 3)
        self.assertEqual(planner.plan_schema(4, 9)["properties"]["tasks"]["minItems"], 4); self.assertEqual(planner.plan_schema(10, 0)["properties"]["tasks"]["minItems"], 1)
    def test_an_item_with_no_usable_reply_is_planned_from_the_specs_own_examples(self):
        slug = {"title": "Add slugify", "files": ["textutil.py"], "size": "S", "deps": [], "asserts": [{"type": "call", "module": "textutil", "func": "slugify", "args": ["A B"], "expect": "a-b"}]}
        self.start([slug, "not json", "still not json"])
        open(os.path.join(self.state, "SPEC.md"), "w").write("# Text\nPython 3.\n\n1. `textutil.py` provides `slugify(text)`.\n2. `textutil.py` provides `shout(text)`: `shout(\"hi\")` is `\"HI\"`.\n")
        info = planner.plan_auto(self.home, self.name, 600, log=lambda *_: None); self.assertTrue(info["ok"], info)
        _, tasks = planparse.parse_plan(self.read("PLAN.md")); self.assertEqual([t["title"] for t in tasks], ["Add slugify", "`textutil.py` provides `shout(text)`: `shout(\"hi\")` is `\"HI\"`."])
        self.assertEqual(K.read_spec(self.proj(), "T2"), [{"type": "call", "module": "textutil", "func": "shout", "args": ["hi"], "expect": "HI"}])
        self.assertTrue(any(json.loads(l).get("op") == "item_from_examples" for l in open(os.path.join(self.state, "plan.log"))))
    def test_per_item_planning_asks_once_per_item_and_leaves_out_an_unusable_item(self):
        slug = {"title": "Add slugify", "files": ["textutil.py"], "size": "S", "deps": [], "asserts": [{"type": "call", "module": "textutil", "func": "slugify", "args": ["A B"], "expect": "a-b"}]}
        title = {"title": "Add title", "files": ["textutil.py"], "size": "S", "deps": [1], "asserts": [{"type": "call", "module": "textutil", "func": "title", "args": ["a"], "expect": "A"}]}
        self.start([slug, "not json", "still not json", title])                     # item 2 fails twice: left out; item 3's dep on item 1 survives the renumbering
        open(os.path.join(self.state, "SPEC.md"), "w").write("# Text utils\nPython 3.\n\n1. Add slugify.\n2. Add shout.\n3. Add title.\n")
        info = planner.plan_auto(self.home, self.name, 600, log=lambda *_: None); self.assertTrue(info["ok"], info)
        self.assertEqual(len(self.fake.requests), 4); self.assertIn("item 1 of 3", self.fake.requests[0]["messages"][1]["content"])
        self.assertEqual(self.fake.requests[0]["format"], planner.task_schema()); self.assertNotIn("Also give", self.fake.requests[0]["messages"][0]["content"])
        self.assertIn("item 2", info["rounds"][0]["error"])
        _, tasks = planparse.parse_plan(self.read("PLAN.md")); self.assertEqual([t["title"] for t in tasks], ["Add slugify", "Add title"]); self.assertEqual(tasks[1]["deps"], ["T1"])
        self.assertIn("# Goal\nText utils", self.read("PLAN.md"))
        self.assertTrue(os.path.exists(os.path.join(self.state, "plan.reply.1.item2.2.json")))
        self.assertEqual(json.loads(self.read("spec_items.json")), {"T1": 1, "T2": 3})        # the requirement each task implements (item 2 was left out), for the worker's context
    def test_after_the_last_round_the_good_tasks_are_kept(self):
        good = {"title": "Add slugify", "files": ["textutil.py"], "size": "S", "deps": [], "asserts": [{"type": "call", "module": "textutil", "func": "slugify", "args": ["A B"], "expect": "a-b"}]}
        holds = {"title": "Keep README", "files": ["README.md"], "size": "S", "deps": [1], "asserts": [{"type": "file_exists", "path": "README.md"}]}    # already holds: rejected every round
        later = {**good, "title": "Add title", "deps": [2], "asserts": [{"type": "call", "module": "textutil", "func": "title", "args": ["a"], "expect": "A"}]}
        still = {"tasks": [{"id": 2, "asserts": [{"type": "file_exists", "path": "README.md"}]}]}
        self.start([good, holds, later, still, still])
        open(os.path.join(self.state, "SPEC.md"), "w").write("1. Add slugify.\n2. Keep the README.\n3. Add title.\n")
        info = planner.plan_auto(self.home, self.name, 600, log=lambda *_: None)
        self.assertTrue(info["ok"], info); self.assertEqual(info["dropped_tasks"], ["Keep README"]); self.assertEqual(len(info["rounds"]), 3)
        _, tasks = planparse.parse_plan(self.read("PLAN.md")); self.assertEqual([t["title"] for t in tasks], ["Add slugify", "Add title"]); self.assertEqual(tasks[1]["deps"], [])
        self.assertEqual(json.loads(self.read("spec_items.json")), {"T1": 1, "T2": 3})        # renumbered with the plan
        self.assertFalse(os.path.exists(os.path.join(self.state, "checks", "T3.spec.json")))
        self.assertTrue(any(json.loads(l).get("op") == "plan_salvaged" for l in open(os.path.join(self.state, "plan.log"))))
    def test_a_bad_assertion_costs_only_itself_not_its_task(self):
        plan = {"goal": "g", "context": "c", "done": "d", "tasks": [{"title": "t", "files": [], "size": "S", "asserts": [
            {"type": "call", "module": "textutil", "func": "slugify", "args": ["A B"], "expect": "a-b"}, {"type": "run", "argv": ["python3", "-c", "print(1))"], "exit": 0}]}]}
        self.start([]); self.assertEqual(planner._write(self.proj(), plan), {})
        self.assertEqual([a["type"] for a in K.read_spec(self.proj(), "T1")], ["call"])
        self.assertTrue(any(json.loads(l).get("op") == "assertions_salvaged" for l in open(os.path.join(self.state, "plan.log"))))
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
    def test_a_child_whose_assertions_already_hold_is_dropped_and_the_failing_child_kept(self):
        K.ensure_runner(self.proj()); holds = [{"title": "readme", "asserts": [{"type": "file_exists", "path": "README.md"}]}, self.SUBS[1]]
        kids, fx = splitter.build(self.proj(), "T1", holds)
        r = planops.apply(self.proj(), {"op": "split_task", "task": "T1", "subtasks": kids, "fixtures": fx, "reason": "r"}, "harness")
        self.assertTrue(r["ok"], r); self.assertEqual(r["result"], ["T1.2"]); self.assertEqual(r["dropped"], ["T1.1:passes"])
        self.assertFalse(os.path.exists(os.path.join(self.state, "checks", "T1.1.spec.json"))); self.assertTrue(os.path.exists(os.path.join(self.state, "checks", "T1.2.spec.json")))
        self.assertEqual(C.verify(self.proj(), T.TaskStore(self.proj()).load()), [])                  # the kept child's spec is frozen and locked
    def test_a_split_where_every_child_already_holds_is_rejected_and_leaves_nothing(self):
        K.ensure_runner(self.proj()); holds = [{"title": "readme", "asserts": [{"type": "file_exists", "path": "README.md"}]}] * 2
        kids, fx = splitter.build(self.proj(), "T1", holds)
        r = planops.apply(self.proj(), {"op": "split_task", "task": "T1", "subtasks": kids, "fixtures": fx, "reason": "r"}, "harness")
        self.assertFalse(r["ok"]); self.assertIn("no subtask", r["error"]); self.assertFalse([n for n in os.listdir(os.path.join(self.state, "checks")) if n.startswith("T1.") and n.endswith(".spec.json")])

    def test_a_child_that_repeats_its_parent_or_an_earlier_child_is_not_a_smaller_step(self):
        # 2026-10-17 calc_go control rerun: T4 -> T4.1 -> T4.1.1 -> T4.1.1.1 -> T4.1.1.1.1 each carried T4's six (impossible) assertions, and siblings
        # with the same assertions as the one that passed were then counted as verified without any work
        parent = self.SUBS[1]["asserts"] + self.SUBS[0]["asserts"]
        same = {"title": "same as the parent", "asserts": list(reversed(parent))}
        more = {"title": "parent plus one", "asserts": parent + [{"type": "file_exists", "path": "x.py"}]}
        twin = {"title": "twin", "asserts": self.SUBS[0]["asserts"]}
        K.ensure_runner(self.proj()); notes = []; kids, fx = splitter.build(self.proj(), "T1", [same, more, self.SUBS[0], twin], [], parent, notes)
        self.assertEqual([k["title"] for k in kids], ["slugify"]); self.assertEqual(sorted(fx), ["T1.1.spec.json"])
        self.assertEqual(sum("not a smaller step" in n for n in notes), 2); self.assertTrue(any("earlier child" in n for n in notes))
    def test_no_child_gets_an_assertion_that_failed_in_every_attempt(self):
        stuck = self.SUBS[1]["asserts"][0]
        kid = {"title": "both", "asserts": [stuck, self.SUBS[0]["asserts"][0]]}; only = {"title": "only the stuck one", "asserts": [stuck]}
        K.ensure_runner(self.proj()); notes = []; kids, fx = splitter.build(self.proj(), "T1", [kid, only], [], None, notes, avoid=[stuck])
        self.assertEqual([k["title"] for k in kids], ["both"]); self.assertEqual(json.loads(fx["T1.1.spec.json"])["asserts"], self.SUBS[0]["asserts"])
        self.assertTrue(any("failed in every attempt" in n for n in notes))

    def test_build_repairs_with_the_parent_as_context_and_keeps_a_single_usable_child(self):
        parent = [{"type": "call", "module": "textutil", "func": "slugify", "args": [x], "expect": x.lower()} for x in ("A", "B", "C")]
        kids_in = [{"title": "empty input", "asserts": [{"type": "call", "module": "textutil", "func": "slugify", "args": ["", ""], "expect": ""}]},       # repaired via the parent's arity
                   {"title": "broken", "asserts": [{"type": "run", "argv": ["python3", "-c", "print(1))"], "exit": 0}]}]                                 # no usable assertion: dropped
        notes = []; kids, fx = splitter.build(self.proj(), "T1", kids_in, ["textutil.py"], parent, notes)
        self.assertEqual(len(kids), 1); self.assertEqual(json.loads(fx["T1.1.spec.json"])["asserts"][0]["args"], [""])
        self.assertTrue(any("removed an extra last input" in n for n in notes)); self.assertTrue(any("dropped child" in n for n in notes))
        r = planops.apply(self.proj(), {"op": "split_task", "task": "T1", "subtasks": kids, "fixtures": fx, "reason": "r"}, "harness"); self.assertTrue(r["ok"], r)
        self.assertEqual(r["result"], ["T1.1"])                                                                                                           # a one-child split is accepted

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

CALC_SPEC = """# Command-line calculator in Go

Build a command-line calculator in Go. All code is in package `main`; `go build -o calc .` must produce the program `calc`.

## Requirements

1. The program `calc` takes an expression as its arguments: `calc 2 + 3` prints `5`. A single number is valid: `calc 7` prints `7`.

2. The letter `x` is another way to write `*`: `calc 6 x 7` prints `42`.

3. Errors print a message starting with `error:` to standard error and exit with status 2.
"""
PY_SPEC = """# Utilities
Python 3, standard library only.

## Requirements

1. `textutil.py` provides `slugify(text)`: `slugify("Hello World")` is `"hello-world"`, and `slugify("")` is `""`. If `len(text) > 100` it is cut.

2. `numutil.py` provides `is_prime(n)`: `is_prime(2)` and `is_prime(97)` are `True`; `is_prime(1)`, `is_prime(0)` and `is_prime(-7)` are `False`. `is_prime("7")` raises `TypeError`.
"""
BUILD = {"type": "run", "argv": ["go", "build", "-o", "calc", "."], "exit": 0}

class TestGrounding(Sandboxed):
    """2026-10-17 design review: the planner's checks are the only judge of the work, and only 3 of the 41 checks the control rerun froze for the Go
    calculator hold on the reference solution. The spec's own worked examples become checks the harness writes itself (sound by construction), and
    model assertions that cannot hold for a program following the spec are dropped before anything is frozen."""
    def test_cli_examples_become_runs_of_the_built_program_after_the_build_step(self):
        items = specdoc.item_texts(CALC_SPEC)
        self.assertEqual(G.examples(CALC_SPEC, items[0]), [BUILD, {"type": "run", "argv": ["./calc", "2", "+", "3"], "stdout": "5"}, {"type": "run", "argv": ["./calc", "7"], "stdout": "7"}])
        self.assertEqual(G.examples(CALC_SPEC, items[1])[1:], [{"type": "run", "argv": ["./calc", "6", "x", "7"], "stdout": "42"}])
        self.assertEqual(G.examples(CALC_SPEC, items[2]), [])                       # prose rules are left to the planner
    def test_call_examples_need_a_module_and_a_declared_function(self):
        items = specdoc.item_texts(PY_SPEC)
        self.assertEqual(G.examples(PY_SPEC, items[0]), [{"type": "call", "module": "textutil", "func": "slugify", "args": ["Hello World"], "expect": "hello-world"},
                                                         {"type": "call", "module": "textutil", "func": "slugify", "args": [""], "expect": ""}])   # `len(text) > 100` is prose
        ex = G.examples(PY_SPEC, items[1])
        self.assertEqual([(a["args"], a.get("expect", a.get("raises"))) for a in ex], [([2], True), ([97], True), ([1], False), ([0], False), ([-7], False), (["7"], "TypeError")])
        self.assertEqual({a["module"] for a in ex}, {"numutil"})
    def test_the_examples_of_every_catalog_spec_hold_on_its_reference_solution(self):
        # the property that makes them safe to freeze without asking the model; level_01, level_03 and calc_go have examples in these phrasings
        from test_w_corpus import judge, REF
        spec = open(os.path.join(REPO, "bench/ladder/catalog/level_01/SPEC.md")).read()
        ex = [a for ex in G.by_item(spec) for a in ex]; self.assertGreater(len(ex), 25)
        self.assertEqual([m for m in judge(ex, REF["level_01"]) if m], [])
    def test_project_kind(self):
        self.assertEqual(G.project_kind(PY_SPEC), "python"); self.assertEqual(G.project_kind(CALC_SPEC), "other")
        self.write_code("go.mod", "module calc\n"); self.assertEqual(G.project_kind("Build a calculator.", self.code), "other")
        self.write_code("tool.py", ""); self.assertEqual(G.project_kind("Build a calculator.", self.code), "python")
    def screen(self, groups, spec=CALC_SPEC, kind="other", prepend=None):
        return G.screen(groups, spec, kind, prepend, [a for ex in G.by_item(spec) for a in ex])
    def test_python_in_a_project_that_is_not_python_is_dropped(self):
        call = {"type": "call", "module": "main", "func": "Add", "args": [1, 2], "expect": 3}; py = {"type": "run", "argv": ["python3", "calc.py", "1"], "stdout": "1"}
        tool = {"type": "run", "argv": ["python3", "-c", "import json; json.load(open('out.json'))"], "exit": 0}; ok = {"type": "run", "argv": ["./calc", "9"], "stdout": "9"}
        out, notes, why = self.screen([[call, py, tool, ok]]); self.assertEqual(out, [[BUILD, tool, ok]]); self.assertEqual(len(why[0]), 2)      # ./calc is built first
        self.assertEqual(self.screen([[call]], PY_SPEC, "python")[0], [[call]])                       # in a Python project a call is the normal test
    def test_options_and_shell_syntax_the_spec_never_mentions_are_dropped_only_from_commands_expected_to_succeed(self):
        flag = {"type": "run", "argv": ["go", "run", ".", "-e", "calc", "-2.5", "+", "1"], "stdout_contains": ["-1.5"]}       # control rerun T4
        echo = {"type": "run", "argv": ["go", "run", ".", "echo", "2 + 3"], "stdout": "5\n", "exit": 0}                       # control rerun T7: stdin through argv
        bad_input = {"type": "run", "argv": ["./calc", "2", "|", "3"], "exit": 2}; neg = {"type": "run", "argv": ["./calc", "-3", "x", "-3"], "stdout": "9"}
        out, _, why = self.screen([[flag, echo, bad_input, neg]]); self.assertEqual(out, [[BUILD, bad_input, neg]])
        self.assertTrue(any("'-e'" in w for w in why[0])); self.assertTrue(any("'echo'" in w and "stdin" in w for w in why[0]))
    def test_an_assertion_that_contradicts_a_spec_example_is_dropped(self):
        wrong = {"type": "run", "argv": ["./calc", "2", "+", "3"], "stdout": "6"}; via_go_run = {"type": "run", "argv": ["go", "run", ".", "6", "x", "7"], "stdout": "40"}
        same = {"type": "run", "argv": ["./calc", "2", "+", "3"], "stdout": "5\n", "exit": 0}
        out, _, why = self.screen([[wrong, same], [via_go_run]]); self.assertEqual(out, [[BUILD, same], []])
        self.assertTrue(all("own example" in w for w in why[0] + why[1]))
    def test_assertions_that_cannot_all_hold_are_dropped_unless_a_majority_agrees(self):
        five = [{"type": "run", "argv": ["go", "run", "."], "stdout": s, "exit": 0} for s in ("5\n", "3.5\n", "0.3333333333333333\n", "0\n", "-1.5\n")]   # control rerun T5
        out, _, why = self.screen([five]); self.assertEqual(out, [[]]); self.assertEqual(len(why[0]), 5)
        err = {"type": "run", "argv": ["go", "run", ".", "10", "/", "0"], "stdout": "", "exit": 1}
        on_stdout = {"type": "run", "argv": ["go", "run", ".", "10", "/", "0"], "stdout_contains": ["error: division by zero"], "exit": 1}       # control rerun T6
        self.assertEqual(self.screen([[err, on_stdout]])[0], [[err]])               # the spec sends `error:` text to standard error, so on_stdout goes first
        plain = dict(err, stdout="oops"); self.assertEqual(self.screen([[err, plain]])[0], [[]])        # a tie nothing breaks: at least one is wrong, both go
        self.assertEqual(G.stderr_only(CALC_SPEC), {"error:"})
        self.assertEqual(G.stderr_only("prints `error: x` to standard error. In batch mode the message (starting with `error:`) goes on standard output"), {"error: x"})
        a3 = {"type": "call", "module": "textutil", "func": "slugify", "args": ["a  b"], "expect": "a-b"}; b = dict(a3, expect="a_b")
        self.assertEqual(self.screen([[a3], [a3], [b]], PY_SPEC, "python")[0], [[a3], [a3], []])       # two tasks agree: the third is dropped
        compatible = [{"type": "run", "argv": ["./calc", "8"], "stdout": "8"}, {"type": "run", "argv": ["./calc", "8"], "stdout_contains": ["8"]}]
        self.assertEqual(self.screen([compatible])[0], [[BUILD] + compatible])
    def test_the_harness_examples_come_first_and_duplicates_are_merged(self):
        items = specdoc.item_texts(CALC_SPEC); ex = G.examples(CALC_SPEC, items[0])
        mine = {"type": "run", "argv": ["./calc", "1", "+", "1"], "stdout": "2"}
        out, notes, _ = self.screen([[ex[1], mine, mine]], prepend=[ex]); self.assertEqual(out, [ex + [mine]]); self.assertEqual(notes, [])
    def test_results_the_toolchain_can_never_show_and_the_programs_own_name_as_an_argument_are_dropped(self):
        two = {"type": "run", "argv": ["go", "run", ".", "+", "3"], "stdout": "", "exit": 2}             # go run reports every failure as exit 1
        named = {"type": "run", "argv": ["go", "run", ".", "calc", "2.5"], "stdout": "2.5\n", "exit": 0}  # 2026-10-16 control: the program's name as an argument
        build = {"type": "run", "argv": ["go", "build", "-o", "calc", "."], "exit": 0}; fmt = {"type": "run", "argv": ["gofmt", "-l", "."], "stdout": ""}
        own = {"type": "run", "argv": ["python3", "-c", "import main; print(main.f())"], "stdout": "1"}; std = {"type": "run", "argv": ["python3", "-c", "import json; print(1)"], "stdout": "1"}
        out, _, why = self.screen([[named, build, fmt, own, std]]); self.assertEqual(out, [[build, fmt, std]]); self.assertEqual(len(why[0]), 2)
        go_run_only = CALC_SPEC.replace("`go build -o calc .` must produce the program `calc`", "it is run with `go run .`")   # no built program to run instead
        out, _, why = self.screen([[two, std]], go_run_only); self.assertEqual(out, [[std]]); self.assertIn("exit status 2 can never be seen", why[0][0])
    def test_go_run_is_rewritten_to_run_the_built_program_unless_it_expects_exit_status_1(self):
        # 2026-10-17 calc3: dropping the malformed-input checks (exit 2 through `go run`) left the exit statuses untested; the program exited 1 for every error
        two = {"type": "run", "argv": ["go", "run", ".", "+", "3"], "stdout": "", "exit": 2}; ok = {"type": "run", "argv": ["go", "run", "main.go", "4"], "stdout": "4"}
        one = {"type": "run", "argv": ["go", "run", ".", "1", "/", "0"], "exit": 1}                       # through go run, 1 means 'failed', with any status
        self.assertEqual(G.via_built(two, CALC_SPEC), {"type": "run", "argv": ["./calc", "+", "3"], "stdout": "", "exit": 2})
        self.assertEqual(G.via_built(ok, CALC_SPEC)["argv"], ["./calc", "4"]); self.assertIs(G.via_built(one, CALC_SPEC), one)
        self.assertIs(G.via_built(two, PY_SPEC), two); self.assertIs(G.via_built(two, "Build it with `gcc -o calc main.c`."), two)      # nothing to build with Go
        out, notes, why = self.screen([[two, ok, one]])
        self.assertEqual(out, [[BUILD, G.via_built(two, CALC_SPEC), G.via_built(ok, CALC_SPEC), one]]); self.assertEqual(dict(why), {})
        self.assertTrue(any(n.startswith("rewrote 2 `go run` assertion(s)") for n in notes))
        wrong = {"type": "run", "argv": ["go", "run", ".", "6", "x", "7"], "stdout": "41", "exit": 0}
        self.assertEqual(self.screen([[wrong]])[0], [[]])                                                # rewritten, it still contradicts `calc 6 x 7` prints `42`
    def test_words_the_spec_passes_as_separate_arguments_are_not_accepted_in_one_argument(self):
        joined = {"type": "run", "argv": ["go", "run", ".", "2 + 3"], "stdout": "5\n", "exit": 0}         # 2026-10-17 calc3 T7: all 12 checks were like this
        lines = {"type": "run", "argv": ["go", "run", ".", "2 + 3\n6 x 7\n"], "stdout": "5\n42\n", "exit": 0}   # standard input, meant
        numbers = {"type": "run", "argv": ["./calc", "12 + 30"], "stdout": "42"}                         # other numbers: the spec passes numbers as arguments
        err = {"type": "run", "argv": ["./calc", "2 + 3"], "exit": 2}                                     # expected to fail, as it does for a program following the spec
        text = {"type": "run", "argv": ["./calc", "hello world"], "stdout": "x"}                          # words the spec never passes: no evidence either way
        stdin = {"type": "run", "argv": ["./calc"], "stdin": "2 + 3\n", "stdout": "5"}
        out, _, why = self.screen([[joined, lines, numbers, err, text, stdin]])
        self.assertEqual(out, [[BUILD, err, text, stdin]]); self.assertEqual(len(why[0]), 3)
        self.assertIn("`calc 2 + 3`", why[0][0]); self.assertIn("stdin field", why[0][0])
        quoted = {"type": "run", "argv": ["python3", "cli.py", "slug", "Hello World"], "stdout": "hello-world"}      # pyutils: the spec quotes one itself
        spec = PY_SPEC + '\n3. `python3 cli.py slug "Hello World"` prints `hello-world`; `python3 cli.py kv a=1 b=2` prints `{"a": 1, "b": 2}`.\n'
        self.assertEqual(G.screen([[quoted]], spec, "python")[0], [[quoted]])
        self.assertEqual(G.spec_commands(spec)[-2:], [(["slug", "Hello World"], 'python3 cli.py slug "Hello World"'), (["kv", "a=1", "b=2"], "python3 cli.py kv a=1 b=2")])
    def test_the_planner_writes_the_specs_examples_first_and_screens_the_models_assertions(self):
        with open(os.path.join(self.state, "SPEC.md"), "w") as f: f.write(CALC_SPEC)
        x9 = {"type": "run", "argv": ["./calc", "3", "x", "3"], "stdout": "9"}
        plan = {"goal": "g", "context": "c", "done": "d", "tasks": [
            {"title": "args", "files": [], "size": "S", "item": 1, "asserts": [{"type": "run", "argv": ["./calc", "2", "+", "3"], "stdout": "6"},
                                                                         {"type": "call", "module": "main", "func": "Add", "args": [2, 3], "expect": 5}]},
            {"title": "x", "files": [], "size": "S", "item": 2, "asserts": [x9]},
            {"title": "errors", "files": [], "size": "S", "item": 3, "asserts": [{"type": "run", "argv": ["go", "run", ".", *e], "stdout_contains": ["error:"], "exit": 2} for e in (["2", "+"], ["+", "2"])]}]}
        bad = planner._write(self.proj(), plan, CALC_SPEC); items = specdoc.item_texts(CALC_SPEC)
        self.assertEqual(K.read_spec(self.proj(), "T1"), G.examples(CALC_SPEC, items[0]))            # the model's two were impossible; the examples remain
        self.assertEqual(K.read_spec(self.proj(), "T2"), G.examples(CALC_SPEC, items[1]) + [x9])
        self.assertEqual(list(bad), [3]); self.assertIn("standard error", bad[3])                    # nothing usable left: the repair round is told why, once
        self.assertEqual(bad[3].count("standard error"), 1)
        log = [json.loads(l) for l in open(os.path.join(self.state, "plan.log")) if "assertions_salvaged" in l][-1]["notes"]
        self.assertTrue(any("own example" in n for n in log)); self.assertTrue(any("not written in Python" in n for n in log)); self.assertTrue(any("examples" in n for n in log))
        self.assertTrue(any(n.startswith("rewrote 2 `go run`") for n in log))
    def test_standard_error_text_expected_on_standard_output_is_dropped_except_with_stdin_or_merged_streams(self):
        spec = CALC_SPEC + "4. Division by zero prints `error: division by zero` to standard error.\n"
        out = {"type": "run", "argv": ["./calc", "1", "/", "0"], "stdout_contains": ["error: division by zero"], "exit": 1}       # 2026-10-17 probe, T6
        piped = dict(out, stdin="1 / 0\n"); merged = {"type": "run", "argv": ["bash", "-c", "./calc 1 / 0 2>&1"], "stdout_contains": ["error: division by zero"], "exit": 1}
        res, _, why = G.screen([[out, piped, merged]], spec, "other", None, []); self.assertEqual(res, [[G.build_step(spec) and {"type": "run", "argv": ["go", "build", "-o", "calc", "."], "exit": 0}, piped, merged]])
        self.assertIn("standard error", why[0][0])
    def test_a_check_that_runs_the_built_program_builds_it_first(self):
        # the binary is git-ignored: a check that only runs ./calc judges whatever build was left behind (a reset to the last checkpoint keeps it)
        run = {"type": "run", "argv": ["./calc", "4"], "stdout": "4"}; build = {"type": "run", "argv": ["go", "build", "-o", "calc", "."], "exit": 0}
        self.assertEqual(G.with_build([run], CALC_SPEC), [build, run]); self.assertEqual(G.with_build([run, build], CALC_SPEC), [build, run])
        self.assertEqual(G.with_build([{"type": "file_exists", "path": "go.mod"}], CALC_SPEC), [{"type": "file_exists", "path": "go.mod"}])
        self.assertEqual(G.with_build([run], PY_SPEC), [run])                                         # nothing to build
        self.assertEqual(self.screen([[run]])[0], [[build, run]])
    def test_context_is_authority_for_a_split(self):
        parent = {"type": "run", "argv": ["./calc", "4", "x", "2"], "stdout": "8"}; child = dict(parent, stdout="6")
        out, _, why = G.screen([[child]], CALC_SPEC, "other", None, [], [parent]); self.assertEqual(out, [[]]); self.assertIn("already in force", why[0][0])

DOUBLER = """# Doubler
Python 3, standard library only.

## Requirements

1. `m.py` provides `f(x)`, which doubles a number: `f(2)` is `4`.
"""

class TestRederive(Sandboxed):
    """2026-10-17 design review: the ladder assumed a failing check means wrong code, so a task whose check could never pass climbed to the split rung
    and the splitter copied the check into smaller tasks (38 of the 41 checks the calc_go control rerun froze could not pass for a correct program).
    Before splitting, the assertions that failed in every attempt are now judged against the spec."""
    EXAMPLE = {"type": "call", "module": "m", "func": "f", "args": [2], "expect": 4}
    WRONG = {"type": "call", "module": "m", "func": "f", "args": [3], "expect": 10}           # the spec says f doubles: f(3) is 6, never 10
    GOOD_F = "open('m.py', 'w').write('def f(x):\\n    return 2 * x\\n')"
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.state, "SPEC.md"), "w") as f: f.write(DOUBLER)
        self.set_env(STUCK_SAME_FAILURE="99", STUCK_NO_PROGRESS="99", THERMAL_GUARD="0")
    def start(self, asserts, worker=None, replies=()):
        K.ensure_runner(self.proj()); K.write_spec(self.proj(), "T1", asserts); specdoc.save_map(self.proj(), {"T1": 1})
        self.write_plan(f"# Tasks\n- [ ] 1. double\n  - check: `{K.command_for('T1')}`\n")
        self.fake = FakeOllama(list(replies)); self.addCleanup(self.fake.stop)
        self.set_env(OLLAMA_PORT=self.fake.port, WORKER_CMD=self.worker(worker or self.GOOD_F)); engine.freeze_project(self.proj())
    def steps(self, n): return [engine.step(self.proj()) for _ in range(n)]
    def task(self): return T.get(T.TaskStore(self.proj()).load(), "T1")
    def log(self, op): return [json.loads(l) for l in open(os.path.join(self.state, "plan.log")) if f'"{op}"' in l]

    def test_an_assertion_the_spec_does_not_require_is_replaced_before_any_split(self):
        self.start([self.EXAMPLE, self.WRONG], replies=[{"verdicts": [{"n": 2, "required_by_spec": False, "why": "f doubles: f(3) is 6"}], "replacements": []}])
        ev = self.steps(4)                                              # rung 1 twice, rung 2 twice: assertion 2 failed every time
        self.assertEqual([e["event"] for e in ev], ["task_failed"] * 4); self.assertEqual(ev[3]["check_rederived"], {"removed": 1, "added": 0})
        self.assertEqual(len(self.fake.requests), 1); req = self.fake.requests[0]
        self.assertEqual(req["format"], RD.schema()); self.assertIn("requirement 1 of 1", req["messages"][1]["content"]); self.assertIn("assertion(s) 2 in every one of its 4 attempts", req["messages"][1]["content"])
        t = self.task(); self.assertEqual(t["check"], K.command_for_spec("T1.r1.spec.json")); self.assertEqual((t["ladder"], t["rederived"], t["attempts_base"]), (1, 1, 4))
        self.assertEqual(t["check_history"][0]["check"], K.command_for("T1")); self.assertEqual(K.read_task_spec(self.proj(), t), [self.EXAMPLE])
        self.assertEqual(C.verify(self.proj(), T.TaskStore(self.proj()).load()), [])            # a harness change, not tampering
        self.assertTrue(os.path.exists(os.path.join(self.state, "checks", "T1.spec.json")))       # the old check stays, frozen, for the record
        self.assertEqual(self.log("check_rederived")[-1]["removed"][0]["assert"], self.WRONG); self.assertTrue(self.log("check_rederived")[-1]["ok"])
        e = engine.step(self.proj()); self.assertEqual((e["event"], e.get("precheck")), ("task_done", True))   # the code already met the spec
    def test_a_required_assertion_stays_and_the_split_leaves_it_out_of_every_child(self):
        seen, hook = os.path.join(self.tmp, "split_task.json"), os.path.join(self.tmp, "split_hook.py")      # a split hook that only records what it was given
        with open(hook, "w") as f: f.write(f"import os, shutil\nshutil.copy(os.environ['CODEBOT_TASK_FILE'], {seen!r})\n")
        self.set_env(SPLIT_CMD=f"{sys.executable} {hook}")
        self.start([self.EXAMPLE, self.WRONG], replies=[{"verdicts": [{"n": 2, "required_by_spec": True, "why": "it is in the spec"}], "replacements": []}])
        ev = self.steps(4); self.assertNotIn("check_rederived", ev[3])
        self.assertEqual(self.task()["check"], K.command_for("T1")); self.assertFalse(self.log("check_rederived")[-1]["ok"])
        self.assertEqual(json.load(open(seen))["avoid"], [self.WRONG])
    def test_the_specs_own_examples_are_never_put_in_question(self):
        self.start([self.EXAMPLE], worker="open('m.py', 'w').write('def f(x):\\n    return x\\n')")      # wrong code: the example fails every time
        self.steps(4); self.assertEqual(self.fake.requests, []); self.assertEqual(self.task()["check"], K.command_for("T1"))
    def test_no_model_call_when_the_tasks_budget_is_nearly_spent(self):
        # 2026-10-17: the judging call used a 60 s floor and overran a task's budget (test_v_capbench's resumed trial)
        self.start([self.EXAMPLE, self.WRONG]); data = T.TaskStore(self.proj()).load(); t = T.get(data, "T1")
        t["history"] = [{"attempt": 1, "failing": [2]}, {"attempt": 2, "failing": [2]}]; T.TaskStore(self.proj()).save(data)
        r = RD.run(self.proj(), "T1", "", timeout=30)
        self.assertFalse(r["changed"]); self.assertEqual(r["avoid"], [self.WRONG]); self.assertEqual(self.fake.requests, [])
    def test_at_most_rederive_max_times(self):
        self.set_env(REDERIVE_MAX="0"); self.start([self.EXAMPLE, self.WRONG])
        self.steps(4); self.assertEqual(self.fake.requests, []); self.assertEqual(self.task()["check"], K.command_for("T1"))
    def test_replacements_are_screened_like_planned_assertions(self):
        bad = {"type": "call", "module": "m", "func": "f", "args": [2], "expect": 5}              # contradicts the spec's own example
        good = {"type": "call", "module": "m", "func": "f", "args": [5], "expect": 10}
        self.start([self.EXAMPLE, self.WRONG], replies=[{"verdicts": [{"n": 2, "required_by_spec": False, "why": "f(3) is 6"}], "replacements": [self.WRONG, bad, good]}])
        ev = self.steps(4); self.assertEqual(ev[3]["check_rederived"], {"removed": 1, "added": 1})
        self.assertEqual(K.read_task_spec(self.proj(), self.task()), [self.EXAMPLE, good])

    def test_failing_numbers_and_persistent_failures(self):
        self.assertEqual(RD.failing_numbers("FAIL: x\nfailing assertions: 2, 5\n2 of 5 assertions failed\n"), [2, 5]); self.assertIsNone(RD.failing_numbers("all 3 assertions hold"))
        h = lambda n, f: {"attempt": n, "failing": f}
        self.assertEqual(RD.persistent({"history": [h(1, [1, 2]), h(2, [2, 3]), h(3, [2])]}), [2])
        self.assertEqual(RD.persistent({"history": [h(1, [2])]}), [])                                  # one attempt is no evidence
        self.assertEqual(RD.persistent({"attempts_base": 2, "history": [h(1, [9]), h(2, [9]), h(3, [1]), h(4, [1])]}), [1])     # only the current check's attempts
        self.assertEqual(RD.persistent({"history": [h(1, [2]), h(2, [])]}), [])                        # it passed once

if __name__ == "__main__": unittest.main()
