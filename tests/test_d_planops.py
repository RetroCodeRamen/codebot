"""F6: the model may add / split / reorder only. Never delete, never touch checks. Everything is logged."""
import json, os, unittest
from helpers import Sandboxed, PLAN2
from codebot import engine, planops, tasks as T, checks as C

COUNT_WORKER = """
import os
with open(os.path.join(os.environ['CODEBOT_STATE'], 'worker_calls'), 'a') as f: f.write(os.environ['CODEBOT_TASK_ID'] + '\\n')
files = {'T1.1': ('a1.txt', 'x'), 'T1.2': ('a2.txt', 'y'), 'T3': ('c.txt', 'z')}
if os.environ['CODEBOT_TASK_ID'] in files:
    n, c = files[os.environ['CODEBOT_TASK_ID']]; open(n, 'w').write(c + '\\n')
"""

class Base(Sandboxed):
    def setUp(self):
        super().setUp(); self.write_plan(PLAN2); engine.freeze_project(self.proj())
    def op(self, **kw): return planops.apply(self.proj(), kw, "model")
    def data(self): return T.TaskStore(self.proj()).load()
    def ids(self): return [t["id"] for t in self.data()["tasks"]]
    def planlog(self): return [json.loads(l) for l in self.read("plan.log").splitlines()]
    def locked_checks(self): return dict(C.load_lock(self.proj())["checks"])

class TestAllowed(Base):
    def test_add_task_is_frozen_and_logged(self):
        before = self.locked_checks()
        r = self.op(op="add_task", title="make c.txt", check="grep -qx z c.txt", reason="spec also needs c.txt")
        self.assertTrue(r["ok"], r); self.assertEqual(r["result"], ["T3"]); self.assertEqual(self.ids(), ["T1", "T2", "T3"])
        after = self.locked_checks(); self.assertEqual({k: after[k] for k in before}, before)  # old checks untouched
        self.assertIn("T3", after); self.assertEqual(C.verify(self.proj(), self.data()), [])
        self.assertTrue(os.path.exists(os.path.join(self.state, "checks", "T3.sh")))
        last = self.planlog()[-1]; self.assertEqual((last["op"], last["ok"], last["actor"]), ("add_task", True, "model"))
        self.assertIn("c.txt", last["reason"]); self.assertIn("T3", self.read("PLAN.md"))

    def test_add_after(self):
        self.op(op="add_task", title="mid", check="true", after="T1", reason="r")
        self.assertEqual(self.ids(), ["T1", "T3", "T2"])

    def test_added_check_is_tamper_protected(self):
        self.op(op="add_task", title="t", check="grep -qx z c.txt", reason="r")
        self.set_env(WORKER_CMD=self.worker("import os\nopen(os.path.join(os.environ['CODEBOT_CHECKS'],'T3.sh'),'w').write('exit 0')"))
        self.op(op="reorder", order=["T3", "T1", "T2"], reason="do it first")
        e = engine.step(self.proj()); self.assertEqual(e["task"], "T3"); self.assertIn("modified:T3.sh", e["tamper"])

    def test_split_keeps_parent_check_as_final_gate(self):
        r = self.op(op="split_task", task="T1", reason="too big", fixtures={"half.sh": "test -f a1.txt && test -f a2.txt\n"},
                    subtasks=[{"title": "part one", "check": "test -f a1.txt"}, {"title": "part two", "check": "test -f a2.txt"}])
        self.assertTrue(r["ok"], r); self.assertEqual(self.ids(), ["T1.1", "T1.2", "T1", "T2"])
        t1 = T.get(self.data(), "T1"); self.assertEqual(t1["check"], "grep -qx hi a.txt")
        self.assertEqual(t1["deps"], ["T1.1", "T1.2"]); self.assertEqual(T.get(self.data(), "T1.2")["deps"], ["T1.1"])
        self.assertEqual(C.verify(self.proj(), self.data()), [])

    def test_split_children_run_first_then_parent_gate_without_worker(self):
        self.op(op="split_task", task="T1", reason="too big",
                subtasks=[{"title": "one", "check": "test -f a1.txt"}, {"title": "two", "check": "test -f a2.txt"}])
        self.write_code("a.txt", "hi\n")                       # the parent's own check already holds -> gate passes by itself
        self.set_env(WORKER_CMD=self.worker(COUNT_WORKER))
        order = [engine.step(self.proj())["task"] for _ in range(3)]
        self.assertEqual(order, ["T1.1", "T1.2", "T1"])
        calls = self.read("worker_calls").split()
        self.assertEqual(calls, ["T1.1", "T1.2"])              # parent gate needed no worker call
        self.assertEqual(self.data()["tasks"][2]["status"], "done")

    def test_reorder_pending(self):
        self.op(op="add_task", title="c", check="true", reason="r")
        r = self.op(op="reorder", order=["T3", "T1", "T2"], reason="c is easiest"); self.assertTrue(r["ok"], r)
        self.assertEqual(self.ids(), ["T3", "T1", "T2"])

class TestForbidden(Base):
    def assertRejected(self, **kw):
        n = len(self.planlog()); before = self.data()["tasks"]; r = self.op(**kw)
        self.assertFalse(r["ok"], r); self.assertEqual(self.data()["tasks"], before)
        log = self.planlog(); self.assertEqual(len(log), n + 1); self.assertFalse(log[-1]["ok"])
        return r

    def test_delete_rejected(self): self.assertRejected(op="delete_task", task="T1", reason="not needed")
    def test_edit_check_rejected(self): self.assertRejected(op="edit_check", task="T1", check="true", reason="easier")
    def test_set_status_rejected(self): self.assertRejected(op="set_status", task="T1", status="done", reason="finished")
    def test_unknown_op_rejected(self): self.assertRejected(op="rename", task="T1", title="x", reason="r")
    def test_reason_required(self): self.assertIn("reason", self.assertRejected(op="add_task", title="x", check="true")["error"])
    def test_add_without_check_rejected(self): self.assertIn("check", self.assertRejected(op="add_task", title="x", reason="r")["error"])
    def test_split_without_checks_rejected(self):
        self.assertRejected(op="split_task", task="T1", reason="r", subtasks=[{"title": "a"}, {"title": "b"}])
    def test_split_needs_two_to_six(self):
        self.assertRejected(op="split_task", task="T1", reason="r", subtasks=[{"title": "a", "check": "true"}])
    def test_cannot_split_done_task(self):
        self.write_code("a.txt", "hi\n"); engine.step(self.proj())            # precheck passes -> T1 done
        self.assertRejected(op="split_task", task="T1", reason="r", subtasks=[{"title": "a", "check": "true"}, {"title": "b", "check": "true"}])
    def test_reorder_cannot_break_dependencies(self): self.assertRejected(op="reorder", order=["T2", "T1"], reason="r")
    def test_reorder_cannot_move_done(self):
        self.write_code("a.txt", "hi\n"); engine.step(self.proj())
        self.assertRejected(op="reorder", order=["T1", "T2"], reason="r")     # T1 is done, so it is not a legal member
    def test_fixture_cannot_overwrite_locked_file(self):
        self.assertRejected(op="add_task", title="x", check="true", reason="r", fixtures={"T1.sh": "exit 0"})
    def test_unsafe_fixture_name(self):
        self.assertRejected(op="add_task", title="x", check="true", reason="r", fixtures={"../escape.sh": "x"})
    def test_checks_never_change_through_any_rejected_op(self):
        before = self.locked_checks()
        for kw in (dict(op="edit_check", task="T1", check="true", reason="r"), dict(op="delete_task", task="T2", reason="r")): self.op(**kw)
        self.assertEqual(self.locked_checks(), before); self.assertEqual(C.verify(self.proj(), self.data()), [])

class TestPrecheck(Base):
    def test_precheck_pass_marks_done_and_flags_weak_check(self):
        self.write_code("a.txt", "hi\n")
        e = engine.step(self.proj()); self.assertEqual((e["event"], e["task"], e.get("precheck")), ("task_done", "T1", True))
        self.assertTrue(any(l.get("op") == "weak_check" for l in self.planlog()))
    def test_worker_gets_fresh_failure_output(self):
        self.set_env(WORKER_CMD=self.worker("import os\nopen(os.path.join(os.environ['CODEBOT_STATE'],'seen_error'),'w').write(open(os.environ['CODEBOT_LAST_ERROR_FILE']).read() or 'EMPTY')"))
        self.write_code("a.txt", "wrong\n"); engine.step(self.proj())
        seen = self.read("seen_error"); self.assertNotEqual(seen, "EMPTY"); self.assertIn("exited with status", seen)

class TestSplitGuard(Base):
    """Split children go through the same baseline guard as add_task, and inherit the parent's files."""
    def setUp(self): super().setUp(); self.set_env(CHECK_BASELINE_GUARD="1")
    def split(self, c1, c2): return self.op(op="split_task", task="T1", reason="too big", subtasks=[{"title": "one", "check": c1}, {"title": "two", "check": c2}])
    def test_a_child_that_already_passes_is_dropped_and_the_rest_of_the_split_kept(self):
        # 2026-10-16, T2 truncate: one already-passing child rejected the whole split, losing the narrow child aimed at the failure
        r = self.split("cat /dev/null", "test -f a2.txt")                   # child 1 always exits 0: that part is already done
        self.assertTrue(r["ok"], r); self.assertEqual(r["dropped"], ["T1.1:passes"]); self.assertEqual(self.ids(), ["T1.2", "T1", "T2"])
        self.assertEqual(T.get(self.data(), "T1")["deps"], ["T1.2"]); self.assertEqual(T.get(self.data(), "T1")["split_into"], ["T1.2"])
        self.assertTrue(any(l.get("op") == "check_rejected" and l["task"] == "T1.1" for l in self.planlog()))
        self.assertEqual([l["dropped"] for l in self.planlog() if l.get("op") == "split_task"], [["T1.1:passes"]])
    def test_a_broken_child_is_dropped_too(self):
        r = self.split("test -f a1.txt", "no_such_command_xyz"); self.assertTrue(r["ok"], r); self.assertEqual(r["dropped"], ["T1.2:broken"]); self.assertEqual(self.ids(), ["T1.1", "T1", "T2"])
    def test_a_split_with_no_child_that_fails_now_is_rejected(self):
        r = self.split("cat /dev/null", "no_such_command_xyz"); self.assertFalse(r["ok"], r); self.assertIn("no subtask has a check that fails now", r["error"]); self.assertEqual(self.ids(), ["T1", "T2"])
    def test_good_children_inherit_parent_files(self):
        r = self.split("test -f a1.txt", "test -f a2.txt"); self.assertTrue(r["ok"], r)
        self.assertEqual(T.get(self.data(), "T1.1")["files"], ["a.txt"]); self.assertEqual(T.get(self.data(), "T1.2")["files"], ["a.txt"])
    def test_child_keeps_its_own_files_when_given(self):
        r = self.op(op="split_task", task="T1", reason="r", subtasks=[{"title": "one", "check": "test -f a1.txt", "files": ["x.py"]}, {"title": "two", "check": "test -f a2.txt"}])
        self.assertTrue(r["ok"], r); self.assertEqual(T.get(self.data(), "T1.1")["files"], ["x.py"])

if __name__ == "__main__": unittest.main()
