"""F4: retry-with-error -> reset+fresh -> split -> alternate prompt -> (larger model) -> blocked. Blocked never stops the run."""
import json, os, re, unittest
from helpers import Sandboxed
from codebot import engine, tasks as T

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
ENV_DUMP = """
import os, json
with open(os.path.join(os.environ['CODEBOT_STATE'], 'env_seen.jsonl'), 'a') as f:
    f.write(json.dumps({k: os.environ.get(k, '') for k in ('CODEBOT_ATTEMPT','CODEBOT_RUNG','CODEBOT_VARIANT','CODEBOT_TEMPERATURE','CODEBOT_MODEL')}) + '\\n')
"""
QUIET = dict(STUCK_SAME_FAILURE=99, STUCK_NO_PROGRESS=99, PINGPONG_WINDOW=0, TASK_MAX_ATTEMPTS=99)

class Base(Sandboxed):
    def setUp(self): super().setUp(); self.write_plan(PLAN); engine.freeze_project(self.proj())
    def cfg(self, **kv): self.set_env(**{k: str(v) for k, v in kv.items()})
    def data(self): return T.TaskStore(self.proj()).load()
    def task(self, tid): return T.get(self.data(), tid)
    def run_until(self, pred, limit=30):
        out = []
        for _ in range(limit):
            e = engine.step(self.proj()); out.append(e)
            if pred(e): break
        return out
    def plan_log(self): return [json.loads(l) for l in self.read("plan.log").splitlines()]
    def rungs(self): return [json.loads(l)["rung"] for l in self.read("attempts.jsonl").splitlines() if '"attempt"' in l and json.loads(l).get("task") == "T1"]
    def env_seen(self): return [json.loads(l) for l in self.read("env_seen.jsonl").splitlines()]

class TestWalk(Base):
    def test_full_walk_without_split_or_big_model(self):
        self.cfg(**QUIET, WORKER_CMD=self.worker(NOTHING))
        ev = self.run_until(lambda e: e["event"] == "task_blocked")
        self.assertEqual(ev[-1]["event"], "task_blocked")
        self.assertEqual(self.rungs(), [1, 1, 2, 2, 4, 4])                        # split unavailable and no big model: skipped
        reasons = [l["reason"] for l in self.plan_log() if l["op"] == "escalate"]
        self.assertEqual(len(reasons), 4)
        self.assertIn("no split available", " ".join(reasons))
        self.assertEqual(self.task("T1")["status"], "blocked")

    def test_big_model_hook_is_a_rung_before_blocking(self):
        self.cfg(**QUIET, ESCALATE_MODEL="big-local-model", WORKER_CMD=self.worker(ENV_DUMP))
        self.run_until(lambda e: e["event"] == "task_blocked")
        self.assertEqual(self.rungs(), [1, 1, 2, 2, 4, 4, 5])
        seen = self.env_seen(); self.assertEqual(seen[-1]["CODEBOT_MODEL"], "big-local-model")
        self.assertTrue(all(s["CODEBOT_MODEL"] == "" for s in seen[:-1]))

    def test_alternate_prompt_rung_sets_variant_and_temperature(self):
        self.cfg(**QUIET, WORKER_CMD=self.worker(ENV_DUMP))
        self.run_until(lambda e: e["event"] == "task_blocked"); seen = self.env_seen()
        by_rung = {s["CODEBOT_RUNG"]: s for s in seen}
        self.assertEqual(by_rung["1"]["CODEBOT_VARIANT"], ""); self.assertEqual(by_rung["2"]["CODEBOT_VARIANT"], "")
        self.assertEqual((by_rung["4"]["CODEBOT_VARIANT"], by_rung["4"]["CODEBOT_TEMPERATURE"]), ("alt", "0.8"))

    def test_ladder_tries_are_configurable(self):
        self.cfg(**QUIET, LADDER_TRIES="1,1,0,1,1", WORKER_CMD=self.worker(NOTHING))
        self.run_until(lambda e: e["event"] == "task_blocked"); self.assertEqual(self.rungs(), [1, 2, 4])

class TestStopsWhereItSucceeds(Base):
    def test_success_at_retry(self):
        self.cfg(**QUIET, WORKER_CMD=self.worker("import os\nif int(os.environ['CODEBOT_ATTEMPT']) == 2: open('a.txt','w').write('hi\\n')"))
        ev = self.run_until(lambda e: e["event"] == "task_done"); self.assertEqual(self.rungs(), [1, 1])
        self.assertEqual(self.task("T1")["status"], "done")

    def test_success_at_fresh_rung_after_reset(self):
        # attempts 1-2 leave junk and fail; attempt 3 (rung 2) must find a clean tree, then succeeds
        self.cfg(**QUIET, WORKER_CMD=self.worker("""
            import os
            n = int(os.environ['CODEBOT_ATTEMPT'])
            if n < 3: open('junk%d.txt' % n, 'w').write('x')
            else:
                assert not any(f.startswith('junk') for f in os.listdir('.')), 'tree was not reset'
                open('a.txt', 'w').write('hi\\n')
        """))
        ev = self.run_until(lambda e: e["event"] == "task_done")
        self.assertEqual(ev[-1]["event"], "task_done", ev[-1]); self.assertEqual(self.rungs(), [1, 1, 2])

    def test_hard_cap_blocks_even_with_rungs_left(self):
        self.cfg(**{**QUIET, "TASK_MAX_ATTEMPTS": 3}, WORKER_CMD=self.worker(NOTHING))
        ev = self.run_until(lambda e: e["event"] == "task_blocked"); self.assertEqual(len(ev), 3)

class TestBlocked(Base):
    def block_t1(self):
        self.cfg(**QUIET, WORKER_CMD=self.worker("import os\nif os.environ['CODEBOT_TASK_ID'] == 'T2': open('b.txt','w').write('yo\\n')"))
        return self.run_until(lambda e: e["event"] == "task_blocked")[-1]

    def test_question_is_specific(self):
        e = self.block_t1(); q = e["question"]
        self.assertIn("T1", q); self.assertIn("make a.txt say hi", q); self.assertIn("codebot answer t1", q); self.assertIn("ladder", q)
        self.assertEqual(self.task("T1")["blocked_question"], q)

    def test_run_continues_to_next_unblocked_task(self):
        self.block_t1(); e = engine.step(self.proj()); self.assertEqual((e["event"], e["task"]), ("task_done", "T2"))
        self.assertEqual(engine.step(self.proj())["event"], "done_with_blocked")

    def test_dependents_wait(self):
        from codebot import planops
        planops.apply(self.proj(), {"op": "add_task", "title": "later", "check": "true", "deps": ["T1"], "reason": "needs T1"})
        self.block_t1(); engine.step(self.proj())                       # T2 done
        self.assertEqual(engine.step(self.proj())["event"], "stalled")

class TestAnswer(Base):
    def setUp(self):
        super().setUp(); self.cfg(**QUIET, WORKER_CMD=self.worker(NOTHING)); self.run_until(lambda e: e["event"] == "task_blocked")
    def test_answer_unblocks_and_resets_the_ladder(self):
        before = self.task("T1")["attempts"]; r = engine.answer(self.proj(), "T1: the file must say hi in lowercase")
        self.assertEqual(r["released"], "T1"); t = self.task("T1")
        self.assertEqual((t["status"], t["ladder"], t["rung_tries"], t["attempts_base"]), ("pending", 1, 0, before))
        self.assertIn("lowercase", self.read("ANSWERS.md")); self.assertNotIn("T1:", self.read("ANSWERS.md").split("answer")[-1][:12])
        self.assertTrue(any(l["op"] == "answer" for l in self.plan_log()))

    def test_budget_is_fresh_after_an_answer(self):
        self.cfg(**{**QUIET, "TASK_MAX_ATTEMPTS": 7}); engine.answer(self.proj(), "try again")      # no id: first blocked task
        e = engine.step(self.proj()); self.assertEqual(e["event"], "task_failed")                    # not instantly re-blocked by the old attempts

    def test_answer_with_nothing_blocked_is_just_recorded(self):
        engine.answer(self.proj(), "first"); r = engine.answer(self.proj(), "second")
        self.assertIsNone(r["released"]); self.assertIn("second", self.read("ANSWERS.md"))

class TestRecovery(Base):
    def test_killed_step_is_not_charged(self):
        from codebot.tasks import TaskStore
        s = TaskStore(self.proj()); d = s.load(); d["tasks"][0]["status"] = "running"; d["tasks"][0]["attempts"] = 1; s.save(d)
        self.cfg(WORKER_CMD=self.worker(NOTHING)); engine.step(self.proj())
        self.assertEqual(self.task("T1")["attempts"], 1)                  # the dead attempt was refunded, this one counts
        self.assertIn("interrupted", self.read("attempts.jsonl"))

if __name__ == "__main__": unittest.main()
