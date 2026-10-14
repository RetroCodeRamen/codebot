"""F5: split what keeps failing, learn what size works, tell the planner."""
import json, os, unittest
from helpers import Sandboxed
from codebot import engine, sizing as Z, tasks as T, planops, checks as C

PLAN = """
# Goal
x
# Tasks
- [ ] 1. write the pair of modules
  - check: `test -f a.py && test -f b.py`
  - files: a.py, b.py
  - size: M
- [ ] 2. fix output
  - check: `grep -q "a && b" notes.txt && test -f c.txt`
"""
class Base(Sandboxed):
    def setUp(self): super().setUp(); self.write_plan(PLAN); engine.freeze_project(self.proj())
    def data(self): return T.TaskStore(self.proj()).load()
    def task(self, tid): return T.get(self.data(), tid)

class TestSplit(Base):
    def test_split_by_files(self):
        r = Z.auto_split(self.proj(), "T1"); self.assertTrue(r["ok"], r)
        self.assertEqual(r["children"], ["T1.1", "T1.2"])
        kid = self.task("T1.1"); self.assertEqual(kid["files"], ["a.py"]); self.assertIn("py_compile", kid["check"])
        self.assertEqual(self.task("T1")["check"], "test -f a.py && test -f b.py")      # parent check kept as the gate
        self.assertEqual(C.verify(self.proj(), self.data()), [])
        self.assertEqual(C.run_check(self.proj(), kid)["rc"], 1)                         # child check starts red

    def test_split_by_check_conjuncts_is_quote_aware(self):
        r = Z.auto_split(self.proj(), "T2"); self.assertTrue(r["ok"], r)
        self.assertEqual([self.task(c)["check"] for c in r["children"]], ['grep -q "a && b" notes.txt', "test -f c.txt"])

    def test_unsplittable_task(self):
        planops.apply(self.proj(), {"op": "add_task", "title": "single", "check": "test -f z", "reason": "r"})
        r = Z.auto_split(self.proj(), "T3"); self.assertFalse(r["ok"]); self.assertIn("no split", r["error"])

    def test_cannot_split_twice(self):
        Z.auto_split(self.proj(), "T1"); self.assertFalse(Z.auto_split(self.proj(), "T1")["ok"])

    def test_split_cmd_plugin(self):
        self.set_env(SPLIT_CMD=self.worker("import json; print(json.dumps({'subtasks':[{'title':'one','check':'test -f x'},{'title':'two','check':'test -f y'}]}))"))
        r = Z.auto_split(self.proj(), "T1"); self.assertTrue(r["ok"], r); self.assertEqual(self.task("T1.2")["check"], "test -f y")

    def test_plugin_proposal_without_checks_is_rejected(self):
        self.set_env(SPLIT_CMD=self.worker("import json; print(json.dumps({'subtasks':[{'title':'one'},{'title':'two'}]}))"))
        r = Z.auto_split(self.proj(), "T1"); self.assertFalse(r["ok"]); self.assertEqual(len(self.data()["tasks"]), 2)

    def test_garbage_plugin_output_is_safe(self):
        self.set_env(SPLIT_CMD=self.worker("print('not json at all')"))
        self.assertFalse(Z.auto_split(self.proj(), "T1")["ok"])

    def test_auto_split_after_k_failures_in_step(self):
        self.set_env(AUTO_SPLIT_AFTER=2, STUCK_SAME_FAILURE=99, WORKER_CMD=self.worker("pass"))
        e1 = engine.step(self.proj()); e2 = engine.step(self.proj())
        self.assertEqual(e1["event"], "task_failed"); self.assertEqual(e2["event"], "task_split"); self.assertEqual(e2["children"], ["T1.1", "T1.2"])
        e3 = engine.step(self.proj()); self.assertEqual(e3["task"], "T1.1")             # children run first

class TestStats(Base):
    def test_outcomes_are_recorded_by_size_and_type(self):
        self.set_env(WORKER_CMD=self.worker("open('a.py','w').write('x=1\\n');open('b.py','w').write('y=1\\n')"))
        engine.step(self.proj())                                                         # T1 done on first try
        s = Z.load_stats(self.proj().p("stats.json"))
        self.assertEqual(s["by_size"]["M"]["done"], 1); self.assertEqual(s["by_size"]["M"]["first_try"], 1)
        self.assertEqual(s["by_type"]["implement"]["tasks"], 1)

    def test_split_and_blocked_are_recorded(self):
        Z.auto_split(self.proj(), "T1")
        self.assertEqual(Z.load_stats(self.proj().p("stats.json"))["by_size"]["M"]["split"], 1)

    def test_machine_wide_stats_aggregate(self):
        Z.record(self.proj(), {"title": "fix thing", "size": "S", "attempts": 1}, "done")
        g = Z.load_stats(os.path.join(self.home, "memory", "sizing.json"))
        self.assertEqual(g["by_size"]["S"]["tasks"], 1); self.assertEqual(g["by_type"]["fix"]["tasks"], 1)

    def test_kind_of(self):
        for t, k in (("write unit tests for x", "test"), ("fix the crash", "fix"), ("refactor parser", "refactor"), ("add endpoint", "implement"), ("update README", "docs")):
            self.assertEqual(Z.kind_of(t), k)

class TestAdvice(Base):
    def feed(self, size, n, first_try):
        for i in range(n): Z.record(self.proj(), {"title": "add thing", "size": size, "attempts": 1 if i < first_try else 3}, "done")
    def test_no_history(self): self.assertIn("no history", Z.advice_line(self.proj()))
    def test_recommends_largest_reliable_size(self):
        self.feed("S", 6, 6); self.feed("M", 6, 5); self.feed("L", 6, 1)
        line = Z.advice_line(self.proj()); self.assertIn("size M or smaller", line); self.assertIn("S: 100%", line)
    def test_unreliable_everything_recommends_s(self):
        self.feed("S", 6, 2); self.feed("M", 6, 1); self.assertIn("size S or smaller", Z.advice_line(self.proj()))
    def test_uses_machine_wide_when_project_has_little(self):
        self.feed("S", 6, 6)
        os.remove(self.proj().p("stats.json"))                                            # project forgot; machine-wide remains
        self.assertIn("S: 100%", Z.advice_line(self.proj()))

if __name__ == "__main__": unittest.main()
