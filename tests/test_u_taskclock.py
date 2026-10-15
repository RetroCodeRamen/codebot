"""Per-task wall-clock budgets: one budget per ROOT task (retries, splits and replans share it), enforced by killing the worker's process group,
persisted across restarts, with timeouts and skipped dependents as terminal states and no project-wide budget."""
import json, os, signal, subprocess, sys, time, unittest
from helpers import Sandboxed
from codebot import engine, planops, taskclock as TC, tasks as T

PLAN = """
# Goal
Test.
# Tasks
- [ ] 1. Create a.txt
  - check: `grep -qx hi a.txt`
- [ ] 2. Create b.txt
  - check: `grep -qx yo b.txt`
- [ ] 3. Create c.txt after a.txt
  - check: `grep -qx c c.txt`
  - deps: 1
"""
# T1 hangs (and leaves a grandchild running), T2 writes its file, T3 would write c.txt
WORKER = """
import os, subprocess, sys, time
tid = os.environ["CODEBOT_TASK_ID"]
if tid.split(".")[0] == "T1":
    p = subprocess.Popen(["sleep", "300"]); open(os.path.join(os.environ["CODEBOT_STATE"], "grandchild.pid"), "w").write(str(p.pid)); time.sleep(300)
elif tid == "T2": open("b.txt", "w").write("yo\\n")
elif tid == "T3": open("c.txt", "w").write("c\\n")
"""

class FakeTime:
    def __init__(self, t=1000.0): self.t = t
    def __call__(self): return self.t

class TestClockUnit(Sandboxed):
    def setUp(self): super().setUp(); self.set_env(TASK_MAX_MINUTES="1")        # 60 s

    def test_root_of_follows_split_children_and_explicit_roots(self):
        self.assertEqual(TC.root_of("T3.1.2"), "T3"); self.assertEqual(TC.root_of({"id": "T9", "budget_root": "T2"}), "T2"); self.assertEqual(TC.root_of({"id": "T4.1"}), "T4")

    def test_budgets_are_off_unless_configured(self):
        s = Sandboxed.__new__(Sandboxed); self.assertEqual(TC.limit_s(self.proj()), 60.0)
        os.environ["TASK_MAX_MINUTES"] = "0"
        try: self.assertIsNone(TC.begin(self.proj(), "T1"))
        finally: del os.environ["TASK_MAX_MINUTES"]

    def test_time_accumulates_across_steps_and_never_resets(self):
        mono, wall = FakeTime(0), FakeTime(1000)
        h = TC.Handle(self.proj(), "T1", mono=mono, wall=wall, heartbeat=0); mono.t += 20; self.assertAlmostEqual(h.remaining(), 40, 1); h.end()
        h2 = TC.Handle(self.proj(), "T1", mono=mono, wall=wall, heartbeat=0)       # a retry is a new interval on the SAME root
        self.assertAlmostEqual(h2.used(), 20, 1); mono.t += 45; self.assertTrue(h2.expired()); self.assertEqual(h2.cap(600), 1); h2.end()
        self.assertAlmostEqual(TC.used(self.proj(), "T1"), 65, 1); self.assertAlmostEqual(TC.summary(self.proj())["T1"]["remaining_s"], -5, 1)

    def test_cap_never_exceeds_what_is_left(self):
        mono = FakeTime(0); h = TC.Handle(self.proj(), "T1", mono=mono, wall=FakeTime(1), heartbeat=0); mono.t += 50
        self.assertEqual(h.cap(600), 11); self.assertEqual(h.cap(5), 5); h.end()

    def test_a_crashed_step_is_charged_up_to_its_last_heartbeat_on_restart(self):
        mono, wall = FakeTime(0), FakeTime(1000)
        h = TC.Handle(self.proj(), "T1", mono=mono, wall=wall, heartbeat=0)          # never ended: the process "died"
        d = TC.load(self.proj()); d["roots"]["T1"]["open"]["hb"] = 1030; TC._save(self.proj(), d)     # its last heartbeat was 30 s in
        self.assertEqual(TC.settle(self.proj()), 1); self.assertAlmostEqual(TC.used(self.proj(), "T1"), 30, 1)
        self.assertAlmostEqual(TC.summary(self.proj())["T1"]["charged_after_crash_s"], 30, 1); self.assertEqual(TC.settle(self.proj()), 0)   # idempotent
        h3 = TC.Handle(self.proj(), "T1", mono=mono, wall=wall, heartbeat=0); self.assertAlmostEqual(h3.used(), 30, 1); h3.end()

    def test_heartbeat_thread_advances_the_persisted_timestamp(self):
        h = TC.Handle(self.proj(), "T1", heartbeat=0.1); t0 = TC.load(self.proj())["roots"]["T1"]["open"]["hb"]; time.sleep(0.45)
        self.assertGreater(TC.load(self.proj())["roots"]["T1"]["open"]["hb"], t0); h.end(); self.assertIsNone(TC.load(self.proj())["roots"]["T1"]["open"])

    def test_thermal_waiting_is_reported_separately_per_root(self):
        with open(self.proj().p("thermal.jsonl"), "w") as f:
            for tid, w in (("T1.2", 40), ("T1", 20), ("T2", 99), (None, 7)): f.write(json.dumps({"event": "resume", "task": tid, "waited": w}) + "\n")
            f.write(json.dumps({"event": "pause", "task": "T1", "temp": 93}) + "\n")
        self.assertEqual(TC.thermal_wait_s(self.proj(), "T1"), 60); self.assertEqual(TC.thermal_wait_s(self.proj(), "T2"), 99)

class TestIndependentBudgets(Sandboxed):
    def test_two_slow_independent_tasks_each_get_a_full_budget(self):
        self.write_plan(PLAN.replace("  - deps: 1\n", "")); engine.freeze_project(self.proj())
        self.set_env(WORKER_CMD=self.worker(WORKER.replace('if tid.split(".")[0] == "T1":', 'if tid in ("T1", "T3"):')), TASK_MAX_MINUTES="0.05")
        t0 = time.time(); events = [engine.step(self.proj()) for _ in range(4)]; el = time.time() - t0
        self.assertEqual([e["event"] for e in events], ["task_timeout", "task_done", "task_timeout", "finished_incomplete"])
        self.assertGreater(el, 5.5)          # two full 3 s budgets were spent inside ONE project: nothing limits the project as a whole

class TestEngineBudgets(Sandboxed):
    def setUp(self):
        super().setUp(); self.write_plan(PLAN); engine.freeze_project(self.proj()); self.set_env(WORKER_CMD=self.worker(WORKER), TASK_MAX_MINUTES="0.05")   # 3 s
    def data(self): return T.TaskStore(self.proj()).load()
    def status(self): return {t["id"]: t["status"] for t in self.data()["tasks"]}
    def alive(self, pid):
        try: os.kill(pid, 0); return True
        except OSError: return False

    def test_a_hanging_task_is_killed_with_its_children_and_marked_timeout(self):
        t0 = time.time(); e = engine.step(self.proj()); el = time.time() - t0
        self.assertEqual(e["event"], "task_timeout"); self.assertEqual(e["root"], "T1"); self.assertLess(el, 20)       # the worker sleeps 300 s: enforced, not logged
        self.assertEqual(self.status(), {"T1": "timeout", "T2": "pending", "T3": "skipped"})
        self.assertEqual(e["skipped"], ["T3"]); self.assertIn("prerequisite T1", self.data()["tasks"][2]["skip_reason"])
        pid = int(open(self.proj().p("grandchild.pid")).read()); time.sleep(0.3)
        self.assertFalse(self.alive(pid) and "sleep" in open(f"/proc/{pid}/cmdline").read(), "the worker's grandchild survived the timeout")

    def test_other_independent_tasks_continue_and_there_is_no_project_wide_budget(self):
        self.assertEqual(engine.step(self.proj())["event"], "task_timeout")
        e = engine.step(self.proj()); self.assertEqual((e["event"], e["task"]), ("task_done", "T2"))
        e = engine.step(self.proj()); self.assertEqual(e["event"], "finished_incomplete"); self.assertEqual(e["counts"]["timeout"], 1); self.assertEqual(e["counts"]["skipped"], 1); self.assertEqual(e["counts"]["done"], 1)
        s = TC.summary(self.proj()); self.assertEqual(set(s), {"T1", "T2"}); self.assertEqual(s["T1"]["state"], "timeout"); self.assertGreater(s["T2"]["remaining_s"], 0)
        self.assertEqual(TC.limit_s(self.proj()), 3.0)       # the only limit that exists is per task

    def test_retries_share_one_budget_and_end_as_timeout_not_failure(self):
        flaky = "import os, time\ntime.sleep(1.4)\n"     # every attempt burns 1.4 s and writes nothing
        self.set_env(WORKER_CMD=self.worker(flaky)); events = []
        for _ in range(4):
            events.append(engine.step(self.proj()))
            if events[-1]["event"] == "task_timeout": break
        self.assertEqual([e["event"] for e in events[:2]], ["task_failed", "task_failed"]); self.assertEqual(events[-1]["event"], "task_timeout"); self.assertLessEqual(len(events), 3)
        self.assertGreaterEqual(TC.used(self.proj(), "T1"), 2.8); self.assertEqual(self.status()["T1"], "timeout")
        self.assertNotEqual(self.status()["T1"], "blocked")

    def test_split_children_and_the_parent_gate_share_the_original_budget(self):
        s = self.proj(); r = planops.apply(s, {"op": "split_task", "task": "T1", "reason": "test", "subtasks": [
            {"title": "half a", "check": "grep -qx hi a.txt", "size": "S"}, {"title": "half b", "check": "grep -qx hi a.txt", "size": "S"}]})
        self.assertTrue(r["ok"], r); self.assertEqual(r["result"], ["T1.1", "T1.2"])
        d = TC.load(s); TC._save(s, {"version": 1, "roots": {"T1": {**TC._root({"roots": {}}, "T1"), "used_s": 2.2}}})      # 2.2 of 3 s already spent on T1 before the split
        e = engine.step(s); self.assertEqual(e["event"], "task_timeout"); self.assertEqual(e["root"], "T1")           # the child only had ~0.8 s left
        self.assertEqual({k: v for k, v in self.status().items() if k.startswith("T1")}, {"T1": "timeout", "T1.1": "timeout", "T1.2": "timeout"})
        self.assertEqual(set(TC.load(s)["roots"]), {"T1"}); self.assertLess(time.time() - 0, 1e12)            # no budget was ever created for T1.1 / T1.2

    def test_a_restart_cannot_refund_time_and_an_exhausted_task_never_starts(self):
        s = self.proj(); TC._save(s, {"version": 1, "roots": {"T1": {**TC._root({"roots": {}}, "T1"), "used_s": 3.0}}})
        e = engine.step(s); self.assertEqual(self.status()["T1"], "timeout"); self.assertEqual((e["event"], e["task"]), ("task_done", "T2"))      # T1 never ran again
        self.assertFalse(os.path.exists(s.p("grandchild.pid")))

    def test_a_killed_engine_process_loses_at_most_one_heartbeat_of_charged_time(self):
        self.set_env(TASK_MAX_MINUTES="0.2"); eng = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "bin", "codebot-engine")
        p = subprocess.Popen([sys.executable, eng, "step", self.name, "--home", self.home], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5.2); os.killpg(p.pid, signal.SIGKILL); p.wait()       # the step dies mid-task (reboot, OOM kill)
        try: os.kill(int(open(self.proj().p("grandchild.pid")).read()), signal.SIGKILL)         # the orphaned grandchild of the dead worker
        except (OSError, ValueError): pass
        s = self.proj(); self.assertIsNotNone(TC.load(s)["roots"]["T1"]["open"])           # the interval was left open
        TC.settle(s); u = TC.used(s, "T1"); self.assertTrue(2.5 <= u <= 5.5, u)           # a restart charges what was heartbeated: the clock does not start again at zero
        e = engine.step(s); self.assertGreaterEqual(TC.used(s, "T1"), u)

    def test_a_model_added_task_spends_the_budget_of_the_task_that_added_it(self):
        os.environ["CODEBOT_TASK_ID"] = "T2"
        try: r = planops.apply(self.proj(), {"op": "add_task", "title": "extra", "check": "test -f x", "reason": "t"})
        finally: del os.environ["CODEBOT_TASK_ID"]
        self.assertTrue(r["ok"], r); extra = self.data()["tasks"][-1]; self.assertEqual(TC.root_of(extra), "T2")

    def test_a_terminal_block_skips_dependents_and_finishes_instead_of_waiting_for_a_human(self):
        s = self.proj(); st = T.TaskStore(s)
        with st.locked(): d = st.load(); d["tasks"][0]["status"] = "blocked"; d["tasks"][1]["status"] = "done"; st.save(d)
        self.assertEqual(engine.step(s)["event"], "stalled")                    # default behaviour (a human may answer the block): unchanged
        self.set_env(BLOCK_TERMINAL="1"); e = engine.step(self.proj())
        self.assertEqual(e["event"], "finished_incomplete"); self.assertEqual(self.status(), {"T1": "blocked", "T2": "done", "T3": "skipped"})

    def test_without_a_budget_nothing_changes(self):
        self.set_env(TASK_MAX_MINUTES="0", WORKER_CMD=self.worker("open('a.txt','w').write('hi\\n')\n")); self.assertEqual(engine.step(self.proj())["event"], "task_done")
        self.assertFalse(os.path.exists(self.proj().p("taskclock.json")))

if __name__ == "__main__": unittest.main()
