"""Baseline guard: a check that already passes (or is broken) tests nothing and is rejected before it is frozen."""
import json, os, shutil, subprocess, sys, unittest
from helpers import Sandboxed
from codebot import engine, guard, planops, sandbox, tasks as T, checks as C

ENG = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "bin", "codebot-engine")
def docker_ready(): return bool(shutil.which("docker")) and subprocess.run(["docker", "image", "inspect", "ubuntu:24.04"], capture_output=True).returncode == 0

class Base(Sandboxed):
    def setUp(self): super().setUp(); self.set_env(CHECK_BASELINE_GUARD="1")
    def plan_log(self):
        try: return [json.loads(l) for l in self.read("plan.log").splitlines()]
        except OSError: return []
    def rejected(self): return [l for l in self.plan_log() if l.get("op") == "check_rejected"]
    def frozen(self): return os.path.exists(os.path.join(self.state, "tasks.json"))

class TestFreeze(Base):
    def test_a_check_that_already_passes_is_rejected_and_nothing_is_frozen(self):
        self.write_plan("# Tasks\n- [ ] 1. readme exists\n  - check: `test -f README.md`\n")      # README.md exists at baseline
        with self.assertRaises(SystemExit) as e: engine.freeze_project(self.proj())
        msg = str(e.exception); self.assertIn("already PASSES", msg); self.assertIn("tests nothing", msg); self.assertIn("check-plan", msg)
        self.assertFalse(self.frozen()); self.assertFalse(os.path.exists(os.path.join(self.state, "checks.lock")))
        r = self.rejected(); self.assertEqual(len(r), 1); self.assertEqual((r[0]["task"], r[0]["verdict"]), ("T1", "passes")); self.assertIn("test -f README.md", r[0]["check"])

    def test_a_check_that_fails_now_is_accepted(self):
        self.write_plan("# Tasks\n- [ ] 1. make a.txt\n  - check: `grep -qx hi a.txt`\n"); engine.freeze_project(self.proj())
        self.assertTrue(self.frozen()); self.assertEqual(self.rejected(), [])

    def test_broken_checks_are_rejected(self):
        for bad in ("if then fi", "definitely_not_a_command_xyz --flag"):
            self.write_plan(f"# Tasks\n- [ ] 1. broken\n  - check: `{bad}`\n")
            with self.assertRaises(SystemExit) as e: engine.freeze_project(self.proj())
            self.assertIn("BROKEN", str(e.exception), bad)
        self.assertEqual([r["verdict"] for r in self.rejected()], ["broken", "broken"])

    def test_every_bad_check_is_listed_and_logged_but_good_ones_are_not(self):
        self.write_plan("# Tasks\n- [ ] 1. good\n  - check: `grep -qx hi a.txt`\n- [ ] 2. trivial\n  - check: `true`\n- [ ] 3. also trivial\n  - check: `test -d .`\n")
        with self.assertRaises(SystemExit) as e: engine.freeze_project(self.proj())
        self.assertIn("2 check(s) rejected", str(e.exception)); self.assertEqual([r["task"] for r in self.rejected()], ["T2", "T3"])

    def test_done_tasks_are_not_probed(self):
        self.write_plan("# Tasks\n- [x] 1. already done\n  - check: `true`\n- [ ] 2. todo\n  - check: `grep -qx hi a.txt`\n"); engine.freeze_project(self.proj())
        self.assertEqual(self.rejected(), [])

    def test_explicit_override_freezes_anyway(self):
        self.write_plan("# Tasks\n- [ ] 1. trivial\n  - check: `true`\n"); engine.freeze_project(self.proj(), allow_passing=True); self.assertTrue(self.frozen())

    def test_guard_is_off_when_not_enabled(self):
        self.set_env(CHECK_BASELINE_GUARD="0"); self.write_plan("# Tasks\n- [ ] 1. trivial\n  - check: `true`\n"); engine.freeze_project(self.proj()); self.assertTrue(self.frozen())

    def test_fixture_based_check_that_fails_is_accepted(self):
        os.makedirs(os.path.join(self.state, "checks"))
        with open(os.path.join(self.state, "checks", "t.py"), "w") as f: f.write("import sys; sys.exit(0 if open('a.txt').read().strip()=='hi' else 1)\n")
        self.write_plan("# Tasks\n- [ ] 1. a\n  - check: `python3 $CODEBOT_CHECKS/t.py`\n"); engine.freeze_project(self.proj()); self.assertTrue(self.frozen())

class TestAddTask(Base):
    def setUp(self):
        super().setUp(); self.write_plan("# Tasks\n- [ ] 1. make a.txt\n  - check: `grep -qx hi a.txt`\n"); engine.freeze_project(self.proj())
    def test_model_cannot_add_a_check_that_already_passes(self):
        before = C.verify(self.proj(), T.TaskStore(self.proj()).load())
        r = planops.apply(self.proj(), {"op": "add_task", "title": "x", "check": "test -f README.md", "reason": "extra",
                                        "fixtures": {"helper.sh": "echo hi\n"}}, "model")
        self.assertFalse(r["ok"]); self.assertIn("already PASSES", r["error"]); self.assertEqual([t["id"] for t in T.TaskStore(self.proj()).load()["tasks"]], ["T1"])
        self.assertEqual(before, []); self.assertEqual(C.verify(self.proj(), T.TaskStore(self.proj()).load()), [])          # no orphan fixture left to look like tampering
        self.assertFalse(os.path.exists(os.path.join(self.state, "checks", "helper.sh")))
        rej = self.rejected(); self.assertEqual((len(rej), rej[0]["actor"]), (1, "model"))
    def test_a_failing_check_is_accepted(self):
        r = planops.apply(self.proj(), {"op": "add_task", "title": "x", "check": "grep -qx yo b.txt", "reason": "r"}, "model"); self.assertTrue(r["ok"], r)

class TestCheckPlanCli(Base):
    def run_cli(self, *a): return subprocess.run([sys.executable, ENG, "check-plan", self.name, "--home", self.home, *a], capture_output=True, text=True)
    def test_ok_plan(self):
        self.write_plan("# Tasks\n- [ ] 1. a\n  - check: `grep -qx hi a.txt`\n"); r = self.run_cli(); self.assertEqual(r.returncode, 0); self.assertTrue(r.stdout.strip().endswith("OK")); self.assertIn("a real test", r.stdout)
    def test_rejected_plan_logs_and_exits_nonzero(self):
        self.write_plan("# Tasks\n- [ ] 1. a\n  - check: `true`\n- [ ] 2. no check at all\n"); r = self.run_cli()
        self.assertEqual(r.returncode, 1); self.assertIn("PASSES", r.stdout.upper()); self.assertIn("NO CHECK", r.stdout); self.assertTrue(r.stdout.strip().endswith("REJECTED"))
        self.assertEqual(self.rejected()[0]["actor"], "planner")
    def test_repeated_planner_attempts_are_each_counted(self):
        self.write_plan("# Tasks\n- [ ] 1. a\n  - check: `true`\n"); self.run_cli(); self.run_cli(); self.assertEqual(len(self.rejected()), 2)

@unittest.skipUnless(docker_ready(), "needs docker + ubuntu:24.04")
class TestProbeIsReadOnly(Base):
    def test_probing_cannot_change_the_code(self):
        self.set_env(SANDBOX="docker"); sandbox._MODE.clear()
        r = guard.probe(self.proj(), "echo oops > created_by_probe.txt")
        self.assertFalse(os.path.exists(os.path.join(self.code, "created_by_probe.txt"))); self.assertNotEqual(r["verdict"], "passes")
        sandbox._MODE.clear()


class TestMalformedCheckLine(Base):
    def test_prose_after_the_closing_backtick_is_its_own_verdict(self):
        from codebot.planparse import parse_plan
        _, t = parse_plan("# Tasks\n- [ ] 1. x\n  - check: `python3 main_app.py` prints 42\n- [ ] 2. y\n  - check: `grep -q hi a.txt`\n- [ ] 3. z\n  - check: grep -q hi a.txt\n")
        self.assertEqual((t[0]["check"], t[0]["check_note"]), ("python3 main_app.py", "prints 42")); self.assertNotIn("check_note", t[1]); self.assertEqual((t[2]["check"], "check_note" in t[2]), ("grep -q hi a.txt", False))
    def test_freeze_rejects_it_with_an_actionable_message(self):
        self.write_plan("# Tasks\n- [ ] 1. rename\n  - check: `python3 main_app.py` prints 42\n")
        with self.assertRaises(SystemExit) as e: engine.freeze_project(self.proj())
        msg = str(e.exception); self.assertIn("AFTER the closing backtick", msg); self.assertIn("prints 42", msg)
        r = self.rejected(); self.assertEqual((r[0]["verdict"], r[0]["task"]), ("malformed", "T1")); self.assertFalse(self.frozen())
    def test_the_cli_names_it_malformed(self):
        self.write_plan("# Tasks\n- [ ] 1. x\n  - check: `true` and more words\n")
        r = subprocess.run([sys.executable, ENG, "check-plan", self.name, "--home", self.home], capture_output=True, text=True)
        self.assertIn("MALFORMED", r.stdout); self.assertEqual(r.returncode, 1)

class TestStaticFlaws(Base):
    """Mistakes a real planner made that the shell does not report (the check just fails forever, which looks like a good failing check)."""
    def script(self, name, body):
        os.makedirs(C.checks_dir(self.proj()), exist_ok=True); open(os.path.join(C.checks_dir(self.proj()), name), "w").write(body)
    def verdict(self, check): return guard.probe(self.proj(), check, 30)
    def test_backtick_inside_the_command_is_broken(self):
        r = self.verdict("`python3 $CODEBOT_CHECKS/t1_test.py`"); self.assertEqual(r["verdict"], "broken"); self.assertIn("backtick", r["out"])
    def test_angle_bracket_placeholder_is_broken(self):
        for bad in ("python3 <home/aj/x/checks/t1_test.py>", "python3 <path/to/t1_test.py>", "bash <script>"):
            r = self.verdict(bad); self.assertEqual(r["verdict"], "broken", bad); self.assertIn("angle-bracket", r["out"])
    def test_missing_checks_script_is_broken(self):
        r = self.verdict("python3 $CODEBOT_CHECKS/nope_test.py"); self.assertEqual(r["verdict"], "broken"); self.assertIn("does not exist", r["out"])
    def test_script_hardcoding_the_state_directory_is_broken(self):
        self.script("t1_test.py", f'import sys\nsys.path.insert(0, "{self.state}/code")\nsys.exit(1)\n')
        r = self.verdict("python3 $CODEBOT_CHECKS/t1_test.py"); self.assertEqual(r["verdict"], "broken"); self.assertIn("relative paths", r["out"])
    def test_a_good_relative_script_that_fails_now_is_ok(self):
        self.script("t1_test.py", 'import sys\nsys.path.insert(0, ".")\nprint("missing")\nsys.exit(1)\n')
        r = self.verdict("python3 $CODEBOT_CHECKS/t1_test.py"); self.assertEqual(r["verdict"], "ok", r)
    def test_ordinary_redirect_and_comparison_checks_are_not_flagged(self):
        for good in ("test -f a.txt", "grep -q hi a.txt", "python3 -c 'import sys; sys.exit(1 if 1 < 2 else 0)'", "cat a.txt | grep -q hi"):
            self.assertIsNone(guard.static_flaw(self.proj(), good), good)
    def test_relative_or_absolute_checks_path_is_broken(self):
        for bad in ("python3 checks/t1_test.py", "python3 ./checks/t1_test.py", "bash /x/projects/p/checks/t4.sh"):
            r = self.verdict(bad); self.assertEqual(r["verdict"], "broken", bad); self.assertIn("$CODEBOT_CHECKS", r["out"])
    def test_wrong_interpreter_is_broken(self):
        self.script("t4.sh", "exit 1\n"); self.script("t5.py", "import sys; sys.exit(1)\n")
        for bad in ("python3 $CODEBOT_CHECKS/t4.sh", "bash $CODEBOT_CHECKS/t5.py"):
            r = self.verdict(bad); self.assertEqual(r["verdict"], "broken", bad); self.assertIn("interpreter", r["out"])
    def test_codebot_checks_variable_form_is_not_flagged(self):
        self.script("t1_test.py", "import sys; sys.exit(1)\n")
        for good in ("python3 $CODEBOT_CHECKS/t1_test.py", "python3 ${CODEBOT_CHECKS}/t1_test.py"):
            self.assertIsNone(guard.static_flaw(self.proj(), good), good)

if __name__ == "__main__": unittest.main()
