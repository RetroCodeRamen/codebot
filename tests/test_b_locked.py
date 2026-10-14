"""F1: checks are locked, completion belongs to the harness, tampering is detected and undone."""
import json, os, unittest
from helpers import Sandboxed, PLAN2
from codebot import engine, tasks as T, checks as C

FIX = """
import os
tid = os.environ['CODEBOT_TASK_ID']
open({'T1':'a.txt','T2':'b.txt'}[tid], 'w').write({'T1':'hi','T2':'yo'}[tid] + '\\n')
"""

class Base(Sandboxed):
    def setUp(self):
        super().setUp(); self.write_plan(PLAN2)
    def freeze(self): engine.freeze_project(self.proj())
    def data(self): return T.TaskStore(self.proj()).load()
    def status(self, tid): return T.get(self.data(), tid)["status"]
    def intact(self): return C.verify(self.proj(), self.data()) == []

class TestTamper(Base):
    def test_weakening_the_check_script(self):
        self.freeze()
        self.set_env(WORKER_CMD=self.worker("""
            import os
            open(os.path.join(os.environ['CODEBOT_CHECKS'], 'T1.sh'), 'w').write('exit 0\\n')
        """))
        e = engine.step(self.proj())
        self.assertEqual(e["event"], "task_failed"); self.assertIn("modified:T1.sh", e["tamper"])
        self.assertEqual(self.status("T1"), "pending"); self.assertTrue(self.intact())

    def test_real_fix_plus_tamper_still_fails_then_honest_attempt_passes(self):
        self.freeze()
        self.set_env(WORKER_CMD=self.worker(FIX + """
            open(os.path.join(os.environ['CODEBOT_CHECKS'], 'T1.sh'), 'a').write('# sneaky\\n')
        """))
        e = engine.step(self.proj()); self.assertEqual(e["event"], "task_failed"); self.assertTrue(self.intact())
        self.set_env(WORKER_CMD=self.worker(FIX))  # honest now; a.txt already exists from the sneaky attempt
        e = engine.step(self.proj()); self.assertEqual(e["event"], "task_done")

    def test_new_file_in_checks_is_removed(self):
        self.freeze()
        self.set_env(WORKER_CMD=self.worker("""
            import os
            open(os.path.join(os.environ['CODEBOT_CHECKS'], 'extra.sh'), 'w').write('exit 0\\n')
        """))
        e = engine.step(self.proj()); self.assertIn("unexpected:extra.sh", e["tamper"])
        self.assertFalse(os.path.exists(os.path.join(self.state, "checks", "extra.sh")))

    def test_deleting_a_check_is_restored(self):
        self.freeze()
        self.set_env(WORKER_CMD=self.worker("""
            import os
            os.remove(os.path.join(os.environ['CODEBOT_CHECKS'], 'T2.sh'))
        """))
        e = engine.step(self.proj()); self.assertIn("missing:T2.sh", e["tamper"])
        self.assertTrue(os.path.exists(os.path.join(self.state, "checks", "T2.sh")))

    def test_fixture_file_is_locked(self):
        os.makedirs(os.path.join(self.state, "checks"))
        with open(os.path.join(self.state, "checks", "t1_test.py"), "w") as f: f.write("import sys; sys.exit(0 if open('a.txt').read().strip()=='hi' else 1)\n")
        self.write_plan("# Tasks\n- [ ] 1. make a.txt\n  - check: `python3 $CODEBOT_CHECKS/t1_test.py`\n")
        self.freeze()
        self.set_env(WORKER_CMD=self.worker("""
            import os
            open(os.path.join(os.environ['CODEBOT_CHECKS'], 't1_test.py'), 'w').write('import sys; sys.exit(0)\\n')
        """))
        e = engine.step(self.proj())
        self.assertEqual(e["event"], "task_failed"); self.assertIn("modified:t1_test.py", e["tamper"])

class TestOwnership(Base):
    def test_worker_cannot_mark_tasks_done_in_tasks_json(self):
        self.freeze()
        self.set_env(WORKER_CMD=self.worker("""
            import json, os
            p = os.path.join(os.environ['CODEBOT_STATE'], 'tasks.json'); d = json.load(open(p))
            for t in d['tasks']: t['status'] = 'done'
            json.dump(d, open(p, 'w'))
        """))
        e = engine.step(self.proj())
        self.assertEqual(e["event"], "task_failed"); self.assertIn("tasks.json:edited-outside-engine", e["tamper"])
        self.assertEqual(self.status("T1"), "pending"); self.assertEqual(self.status("T2"), "pending")

    def test_worker_cannot_change_check_text_in_tasks_json(self):
        self.freeze()
        self.set_env(WORKER_CMD=self.worker("""
            import json, os
            p = os.path.join(os.environ['CODEBOT_STATE'], 'tasks.json'); d = json.load(open(p))
            d['tasks'][0]['check'] = 'true'; json.dump(d, open(p, 'w'))
        """))
        e = engine.step(self.proj()); self.assertEqual(e["event"], "task_failed")
        self.assertEqual(self.data()["tasks"][0]["check"], "grep -qx hi a.txt")

    def test_claiming_done_does_nothing(self):
        self.freeze()
        self.set_env(WORKER_CMD=self.worker("""
            import os
            open(os.path.join(os.environ['CODEBOT_STATE'], 'STATUS'), 'w').write('DONE')
            print('I have completed the task successfully, all tests pass')
        """))
        e = engine.step(self.proj()); self.assertEqual(e["event"], "task_failed"); self.assertEqual(self.status("T1"), "pending")

    def test_honest_run_has_no_tamper(self):
        self.freeze(); self.set_env(WORKER_CMD=self.worker(FIX))
        for _ in range(2):
            e = engine.step(self.proj()); self.assertEqual((e["event"], e["tamper"]), ("task_done", []))
        self.assertEqual(engine.step(self.proj())["event"], "all_done")

    def test_tamper_is_recorded_in_attempt_log(self):
        self.freeze()
        self.set_env(WORKER_CMD=self.worker("import os\nopen(os.path.join(os.environ['CODEBOT_CHECKS'],'T1.sh'),'w').write('exit 0')"))
        engine.step(self.proj())
        recs = [json.loads(l) for l in self.read("attempts.jsonl").splitlines()]
        self.assertTrue(any(r.get("event") == "tamper" for r in recs))
        self.assertTrue(any(r.get("tamper") for r in recs if "attempt" in r))

if __name__ == "__main__": unittest.main()
