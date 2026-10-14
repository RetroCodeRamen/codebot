"""F3: caps and stuck signals. Whatever the worker does, the run terminates and the task is handed off, not looped."""
import json, os, time, unittest
from helpers import Sandboxed
from codebot import engine, budgets as B, tasks as T

# Two independent tasks; the check prints a.txt so the failure signature depends on what the worker wrote.
PLAN = """
# Goal
x
# Tasks
- [ ] 1. make a.txt say hi
  - check: `cat a.txt 2>&1; grep -qx hi a.txt`
- [ ] 2. make b.txt say yo
  - check: `grep -qx yo b.txt`
"""
NOTHING = "pass"
def writer(seq):  # worker writes the Nth content (by attempt number) into a.txt
    return f"import os\nseq = {seq!r}\nopen('a.txt','w').write(seq[(int(os.environ['CODEBOT_ATTEMPT'])-1) % len(seq)] + '\\n')\n"

class Base(Sandboxed):
    def setUp(self):
        super().setUp(); self.write_plan(PLAN); engine.freeze_project(self.proj())
    def cfg(self, **kv): self.set_env(**{k: str(v) for k, v in kv.items()})
    def data(self): return T.TaskStore(self.proj()).load()
    def status(self, tid): return T.get(self.data(), tid)["status"]
    def steps(self, n): return [engine.step(self.proj()) for _ in range(n)]

class TestSignals(Base):
    def test_same_failure_blocks_after_n(self):
        self.cfg(STUCK_SAME_FAILURE=3, WORKER_CMD=self.worker(NOTHING))
        ev = self.steps(3)
        self.assertEqual([e["event"] for e in ev], ["task_failed", "task_failed", "task_blocked"])
        self.assertIn("same_failure", ev[2]["signals"]); self.assertEqual(self.status("T1"), "blocked")
        self.assertIn("Stuck", T.get(self.data(), "T1")["blocked_question"])

    def test_varying_failures_do_not_trigger_same_failure(self):
        self.cfg(STUCK_SAME_FAILURE=3, TASK_MAX_ATTEMPTS=50, STUCK_NO_PROGRESS=50, PINGPONG_WINDOW=0,
                 WORKER_CMD=self.worker(writer(["alpha", "beta", "gamma", "delta"])))
        ev = self.steps(4); self.assertTrue(all(e["event"] == "task_failed" for e in ev), ev)

    def test_no_progress_across_attempts(self):
        self.cfg(STUCK_SAME_FAILURE=99, TASK_MAX_ATTEMPTS=99, PINGPONG_WINDOW=0, STUCK_NO_PROGRESS=4,
                 WORKER_CMD=self.worker(writer(["w1", "w2", "w3", "w4", "w5"])))
        ev = self.steps(4)
        self.assertEqual(ev[3]["event"], "task_blocked"); self.assertIn("no_progress", ev[3]["signals"])

    def test_edit_ping_pong(self):
        self.cfg(STUCK_SAME_FAILURE=99, TASK_MAX_ATTEMPTS=99, STUCK_NO_PROGRESS=99, PINGPONG_WINDOW=6,
                 WORKER_CMD=self.worker(writer(["first", "second", "first", "second"])))
        ev = self.steps(3)
        self.assertEqual(ev[2]["event"], "task_blocked"); self.assertIn("edit_ping_pong", ev[2]["signals"])
        self.assertIn("a.txt", ev[2]["question"] + " " + str(ev[2]["signals"]) + json.dumps(self.data()))

    def test_ping_pong_not_triggered_by_steady_progress(self):
        self.cfg(STUCK_SAME_FAILURE=99, TASK_MAX_ATTEMPTS=99, STUCK_NO_PROGRESS=99, PINGPONG_WINDOW=6,
                 WORKER_CMD=self.worker(writer(["a", "b", "c", "d", "e"])))
        self.assertTrue(all(e["event"] == "task_failed" for e in self.steps(5)))

    def test_task_attempt_cap(self):
        self.cfg(STUCK_SAME_FAILURE=99, STUCK_NO_PROGRESS=99, PINGPONG_WINDOW=0, TASK_MAX_ATTEMPTS=3,
                 WORKER_CMD=self.worker(writer(["p", "q", "r", "s"])))
        ev = self.steps(3); self.assertEqual(ev[2]["event"], "task_blocked"); self.assertIn("task_budget", ev[2]["signals"])

class TestRunContinues(Base):
    def test_blocked_task_does_not_stop_the_run(self):
        self.cfg(STUCK_SAME_FAILURE=2, WORKER_CMD=self.worker(
            "import os\nif os.environ['CODEBOT_TASK_ID'] == 'T2': open('b.txt','w').write('yo\\n')"))
        ev = self.steps(3)
        self.assertEqual([e["event"] for e in ev], ["task_failed", "task_blocked", "task_done"]); self.assertEqual(ev[2]["task"], "T2")
        self.assertEqual(engine.step(self.proj())["event"], "done_with_blocked")

    def test_dependents_of_a_blocked_task_stall_not_spin(self):
        self.cfg(STUCK_SAME_FAILURE=1, WORKER_CMD=self.worker(NOTHING))
        engine.freeze_project  # keep linter quiet
        planops_op = {"op": "add_task", "title": "later", "check": "true", "deps": ["T1"], "reason": "needs T1"}
        from codebot import planops; planops.apply(self.proj(), planops_op, "test")
        self.steps(2)                                  # T1 blocks, T2 blocks
        self.assertEqual(engine.step(self.proj())["event"], "stalled")

    def test_never_loops_forever(self):
        self.cfg(WORKER_CMD=self.worker(NOTHING))
        for i in range(60):
            e = engine.step(self.proj())
            if e["event"] not in ("task_failed", "task_blocked"): break
        self.assertIn(e["event"], ("done_with_blocked", "stalled")); self.assertLess(i, 20)

class TestProjectBudgets(Base):
    def test_iteration_cap(self):
        self.cfg(MAX_ITERATIONS=2, STUCK_SAME_FAILURE=99, TASK_MAX_ATTEMPTS=99, STUCK_NO_PROGRESS=99, WORKER_CMD=self.worker(NOTHING))
        self.steps(2); e = engine.step(self.proj())
        self.assertEqual((e["event"], e["kind"]), ("budget", "iterations"))

    def test_wallclock_cap_does_not_start_a_worker(self):
        with open(os.path.join(self.state, "started.at"), "w") as f: f.write(str(int(time.time()) - 3 * 3600))
        self.cfg(PROJECT_MAX_MINUTES=60, WORKER_CMD=self.worker("open('ran','w').write('1')"))
        e = engine.step(self.proj()); self.assertEqual((e["event"], e["kind"]), ("budget", "wallclock"))
        self.assertFalse(os.path.exists(os.path.join(self.code, "ran")))

    def test_flipped_helper(self):
        self.assertTrue(B._flipped(["a", "b", "a"])); self.assertFalse(B._flipped(["a", "b", "c"]))
        self.assertFalse(B._flipped(["a", "a", "a"])); self.assertTrue(B._flipped(["a", "b", "b", "a"]))

if __name__ == "__main__": unittest.main()
