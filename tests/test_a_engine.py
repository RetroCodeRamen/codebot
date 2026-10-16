import json, os, unittest
from helpers import Sandboxed, PLAN2
from codebot import engine, tasks as T, checks as C
from codebot.planparse import parse_plan

class TestParse(unittest.TestCase):
    def test_parse(self):
        head, tasks = parse_plan(PLAN2)
        self.assertIn("# Goal", head); self.assertNotIn("Tasks", head)
        self.assertEqual([t["id"] for t in tasks], ["T1", "T2"])
        self.assertEqual(tasks[0]["check"], "grep -qx hi a.txt")
        self.assertEqual(tasks[0]["files"], ["a.txt"])
        self.assertEqual(tasks[1]["size"], "S"); self.assertEqual(tasks[1]["deps"], ["T1"])

class TestFreeze(Sandboxed):
    def test_refuses_task_without_check(self):
        self.write_plan("# Tasks\n- [ ] 1. no check here\n")
        with self.assertRaises(SystemExit) as e: engine.freeze_project(self.proj())
        self.assertIn("without a check", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(self.state, "tasks.json")))
    def test_allow_unchecked(self):
        self.write_plan("# Tasks\n- [ ] 1. no check here\n")
        d = engine.freeze_project(self.proj(), allow_unchecked=True); self.assertEqual(len(d["tasks"]), 1)
    def test_freeze_writes_scripts_lock_and_view(self):
        self.write_plan(PLAN2); engine.freeze_project(self.proj())
        s = lambda *a: os.path.join(self.state, *a)
        for f in ("tasks.json", "checks.lock", "checks/T1.sh", "checks/T2.sh", "checks/.frozen/T1.sh", "plan.log"):
            self.assertTrue(os.path.exists(s(f)), f)
        plan = self.read("PLAN.md")
        self.assertIn("- [ ] T1. Create a.txt containing hi", plan); self.assertIn("# Goal", plan)
        self.assertEqual(C.verify(self.proj(), T.TaskStore(self.proj()).load()), [])
    def test_refreeze_refused(self):
        self.write_plan(PLAN2); engine.freeze_project(self.proj())
        with self.assertRaises(SystemExit): engine.freeze_project(self.proj())

class TestStep(Sandboxed):
    def setUp(self):
        super().setUp(); self.write_plan(PLAN2); engine.freeze_project(self.proj())
    def test_no_worker_is_not_done(self):
        e = engine.step(self.proj()); self.assertEqual(e["event"], "task_failed")
    def test_worker_that_fixes_it(self):
        self.set_env(WORKER_CMD=self.worker("""
            import os
            tid = os.environ['CODEBOT_TASK_ID']
            open({'T1':'a.txt','T2':'b.txt'}[tid], 'w').write({'T1':'hi','T2':'yo'}[tid] + '\\n')
        """))
        e1 = engine.step(self.proj()); self.assertEqual((e1["event"], e1["task"]), ("task_done", "T1"))
        e2 = engine.step(self.proj()); self.assertEqual((e2["event"], e2["task"]), ("task_done", "T2"))
        self.assertEqual(engine.step(self.proj())["event"], "all_done")
        plan = self.read("PLAN.md")
        self.assertIn("- [x] T1.", plan); self.assertIn("- [x] T2.", plan)
    def test_a_task_that_breaks_a_done_task_is_not_done(self):
        # the T6-broke-T5 pattern from the first full level-1 run: T2's own check passes but its edit breaks T1
        self.set_env(WORKER_CMD=self.worker("""
            import os
            tid = os.environ['CODEBOT_TASK_ID']; fix = os.path.exists('fixed.flag')
            if tid == 'T1': open('a.txt', 'w').write('hi\\n')
            else:
                open('b.txt', 'w').write('yo\\n'); open('a.txt', 'w').write('hi\\n' if fix else 'oops\\n'); open('fixed.flag', 'w').write('')
        """))
        self.assertEqual(engine.step(self.proj())["event"], "task_done")
        e = engine.step(self.proj()); self.assertEqual((e["event"], e["task"]), ("task_failed", "T2"))
        self.assertIn("broke 1 task(s) that were already done", e["out_tail"]); self.assertIn("T1", e["out_tail"])
        data = T.TaskStore(self.proj()).load(); self.assertEqual(T.get(data, "T1")["status"], "done"); self.assertEqual(T.get(data, "T2")["status"], "pending")
        rec = [json.loads(l) for l in self.read("attempts.jsonl").splitlines() if '"regressed"' in l]; self.assertEqual(rec[0]["regressed"], ["T1"])
        e = engine.step(self.proj()); self.assertEqual((e["event"], e["task"]), ("task_done", "T2"))       # the next attempt keeps T1 working
        self.assertEqual(engine.step(self.proj())["event"], "all_done")
    def test_regression_gate_can_be_switched_off(self):
        self.set_env(REGRESSION_GATE="0", WORKER_CMD=self.worker("""
            import os
            open('a.txt', 'w').write('hi\\n' if os.environ['CODEBOT_TASK_ID'] == 'T1' else 'oops\\n'); open('b.txt', 'w').write('yo\\n')
        """))
        engine.step(self.proj()); self.assertEqual(engine.step(self.proj())["event"], "task_done")
    def test_dependency_order(self):
        # T2 may not run before T1 is done even if its file already exists
        self.write_code("b.txt", "yo\n")
        e = engine.step(self.proj()); self.assertEqual(e["task"], "T1")
    def test_attempts_logged(self):
        engine.step(self.proj()); engine.step(self.proj())
        lines = [json.loads(l) for l in self.read("attempts.jsonl").splitlines()]
        self.assertEqual([l["attempt"] for l in lines if "attempt" in l], [1, 2])
        self.assertTrue(all(l["sig"] for l in lines if "attempt" in l))

if __name__ == "__main__": unittest.main()
