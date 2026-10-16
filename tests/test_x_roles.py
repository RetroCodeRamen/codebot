"""Per-role model and thinking settings (roles.py): defaults reproduce the single-model setup exactly; a role's model and think value reach the
request; a thinking call gets THINK_BUDGET extra output tokens; the audit log records the role."""
import json, os, unittest
from helpers import Sandboxed
from fake_ollama import FakeOllama
from codebot import ollama, planner, roles

TASK = {"title": "Add slugify", "files": ["textutil.py"], "size": "S", "deps": [], "asserts": [{"type": "call", "module": "textutil", "func": "slugify", "args": ["A B"], "expect": "a-b"}]}

class TestRoles(Sandboxed):
    def test_defaults_are_the_single_model_setup(self):
        p = self.proj()
        self.assertEqual({r: roles.model(p, r) for r in roles.ROLES}, {r: "codebot-model" for r in roles.ROLES})
        self.assertEqual({r: roles.think(p, r) for r in roles.ROLES}, {r: False for r in roles.ROLES})
        self.assertIsNone(roles.describe(p)["escalate"])
    def test_values_are_parsed(self):
        self.set_env(CODEBOT_MODEL="w", PLANNER_MODEL="big", PLANNER_THINK="on", SPLITTER_THINK="high", WORKER_THINK="off")
        p = self.proj()
        self.assertEqual((roles.model(p, "planner"), roles.model(p, "splitter"), roles.model(p, "worker")), ("big", "w", "w"))
        self.assertEqual((roles.think(p, "planner"), roles.think(p, "splitter"), roles.think(p, "worker")), (True, "high", False))
    def test_the_planner_role_reaches_the_request_with_a_thinking_budget(self):
        fake = FakeOllama([TASK, TASK]); self.addCleanup(fake.stop)
        self.set_env(OLLAMA_PORT=fake.port, THERMAL_GUARD="0", PLANNER_MODEL="big", PLANNER_THINK="on", THINK_BUDGET="1000")
        with open(os.path.join(self.state, "SPEC.md"), "w") as f: f.write("1. Add slugify.\n2. Add slugify again.\n")
        planner.plan_auto(self.home, self.name, 600, log=lambda *_: None)
        b = fake.requests[0]; self.assertEqual((b["model"], b["think"]), ("big", True)); self.assertEqual(b["options"]["num_predict"], 2500 + 1000)
        with open(os.path.join(self.state, "audit.jsonl")) as f: log = [json.loads(l) for l in f if '"model_request"' in l]
        self.assertEqual(log[0]["role"], "planner"); self.assertTrue(log[0]["think"])
    def test_a_call_without_thinking_is_unchanged(self):
        fake = FakeOllama([{"x": 1}]); self.addCleanup(fake.stop); self.set_env(OLLAMA_PORT=fake.port, THERMAL_GUARD="0")
        ollama.chat(self.proj(), [{"role": "user", "content": "hi"}], num_predict=100)
        b = fake.requests[0]; self.assertIs(b["think"], False); self.assertEqual(b["options"]["num_predict"], 100)

if __name__ == "__main__": unittest.main()
