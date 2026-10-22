"""The design conversation: questions, a plan, then a build, then a closing review. The model is scripted."""
import json, os, time
import helpers
from codebot import ollama, shape
from codebot.config import read_env

SPEC = """# Doubler

Build `mathx.py` in Python 3 (standard library only).

## Requirements

1. `mathx.py` provides `double(n)`, which returns twice `n`: `double(2)` is `4` and `double(-3)` is `-6`.
"""

class ShapeCase(helpers.Sandboxed):
    def setUp(self):
        super().setUp(); self._chat = shape.chat; self.calls = []
    def tearDown(self):
        shape.chat = self._chat; super().tearDown()

    def wait(self, name):
        home = os.path.realpath(self.home); key = (home, name)
        for _ in range(100):
            t = shape._threads.get(key)
            if t: t.join(2)
            if not shape._busy_live(home, name, shape._load(home, name)): return
            time.sleep(0.02)
        self.fail("the step did not finish")

    def script(self, replies):
        box = {"i": 0}
        def fake(proj, messages, schema=None, **k):
            self.calls.append((schema or {}).get("required"))
            r = replies[box["i"]]; box["i"] += 1
            return {"content": json.dumps(r)}
        shape.chat = fake

    def test_two_rounds_then_a_plan(self):
        self.script([
            {"understood": "A function that doubles.", "suggest_security": 1, "questions": [{"ask": "Whole numbers only?", "why": "The examples change."}]},
            {"ready": False, "title": "", "summary": "", "pieces": [], "decisions": [], "spec": "", "questions": [{"ask": "And negative numbers?", "why": "One example can show it."}]},
            {"ready": True, "title": "Doubler", "summary": "Doubles a number.", "pieces": [{"name": "double", "does": "returns twice its input"}],
             "decisions": ["Negatives count."], "questions": [], "spec": SPEC},
        ])
        name = shape.begin(self.home, "I want a tiny function that doubles a number")["name"]
        self.wait(name)
        v = shape.view(self.home, name)
        self.assertEqual(v["phase"], "questions")
        self.assertEqual(v["questions"][0]["ask"], "Whole numbers only?")
        shape.answer(self.home, name, {v["questions"][0]["id"]: "No, negatives too."})
        self.wait(name)
        v = shape.view(self.home, name)
        self.assertEqual(v["phase"], "questions")
        self.assertIn("negative", v["questions"][0]["ask"])
        shape.answer(self.home, name, {v["questions"][0]["id"]: "Yes."})
        self.wait(name)
        v = shape.view(self.home, name)
        self.assertEqual(v["phase"], "ready")
        self.assertEqual(v["outline"]["title"], "Doubler")
        self.assertGreaterEqual(v["score"]["total"], 1)
        self.assertTrue(all(r["status"] == "waiting" or r["status"] == "unchecked" for r in v["checklist"]))

    def test_a_third_round_is_folded_into_the_plan(self):
        ask = {"ask": "One more thing?", "why": "It would change the edge case."}
        self.script([
            {"understood": "A doubler.", "questions": [ask]},
            {"ready": False, "title": "Doubler", "summary": "Almost.", "pieces": [], "decisions": [], "spec": "", "questions": [ask]},
            {"ready": False, "title": "Doubler", "summary": "Doubles a number.", "pieces": [], "decisions": [], "spec": SPEC, "questions": [ask]},
        ])
        name = shape.begin(self.home, "I want a tiny function that doubles a number")["name"]
        self.wait(name)
        shape.answer(self.home, name, {"q1": "No."}); self.wait(name)
        shape.answer(self.home, name, {"q1": "Leave it."}); self.wait(name)
        v = shape.view(self.home, name)
        self.assertEqual(v["phase"], "ready")
        self.assertIn("One more thing?", v["open"])

    def test_build_writes_a_drafts_project_outside_the_checkout(self):
        name = "egg"
        d = os.path.join(self.home, "projects", name); os.makedirs(d)
        with open(os.path.join(d, "project.env"), "w") as f: f.write('WORKFLOW="shape"\nSECURITY_LEVEL="1"\n')
        shape._save(self.home, name, {"phase": "ready", "idea": "boil eggs", "spec": SPEC, "security": 1, "questions": []})
        code = os.path.join(self.tmp, "eggs")
        shape.prepare_build(self.home, name, code, 2, {"DRAFTS_MINUTES": "30"})
        env = read_env(os.path.join(d, "project.env"))
        self.assertEqual(env["WORKFLOW"], "drafts")
        self.assertEqual(env["SECURITY_LEVEL"], "2")
        self.assertEqual(env["WORKDIR"], os.path.realpath(code))
        self.assertEqual(env["DRAFTS_MINUTES"], "30")
        self.assertTrue(os.path.isfile(os.path.join(d, "SPEC.md")))
        st = shape._load(self.home, name); st["phase"] = "ready"; shape._save(self.home, name, st)
        with self.assertRaises(shape.ShapeError) as cm:
            shape.prepare_build(self.home, name, os.path.join(self.home, "inside"))
        self.assertIn("outside", str(cm.exception))

    def test_level_zero_skips_the_security_pass(self):
        self.script([{"summary": "Nothing unused, and nothing races.", "findings": []}])
        self.write_code("mathx.py", "def double(n):\n    return n * 2\n")
        self.set_env(SECURITY_LEVEL="0", WORKFLOW="drafts")
        shape._save(self.home, self.name, {"phase": "building", "idea": "double", "spec": SPEC, "security": 0})
        note = shape.run_review(self.home, self.name)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("no security check", note)
        self.assertEqual(shape.view(self.home, self.name)["phase"], "done")

    def test_level_three_reads_the_code_twice_when_something_is_serious(self):
        self.script([
            {"summary": "A visitor can shape a command.", "findings": [{"where": "main", "what": "The request text is passed to a shell.", "serious": "serious"}]},
            {"summary": "No unused code.", "findings": []},
            {"summary": "The shell call is real.", "findings": [{"where": "main", "what": "The request text is passed to a shell.", "serious": "serious"}]},
        ])
        self.write_code("mathx.py", "import os\ndef go(s):\n    os.system(s)\n")
        self.set_env(SECURITY_LEVEL="3", WORKFLOW="drafts")
        shape._save(self.home, self.name, {"phase": "building", "spec": SPEC, "security": 3})
        shape.run_review(self.home, self.name)
        self.assertEqual(len(self.calls), 3)
        v = shape.view(self.home, self.name)
        self.assertEqual(v["review"]["security"]["level"], 3)
        self.assertIn("second time", v["notice"])
        self.assertIn("internet", shape.security_brief(3))
        self.assertNotIn("internet", shape.security_brief(1))

    def test_a_dead_step_can_be_tried_again(self):
        d = os.path.join(self.home, "projects", "lamp"); os.makedirs(d)
        with open(os.path.join(d, "project.env"), "w") as f: f.write('WORKFLOW="shape"\n')
        shape._save(self.home, "lamp", {"phase": "questions", "idea": "a lamp", "questions": [{"id": "q1", "ask": "Who uses it?", "why": "", "answer": ""}],
                                        "busy": {"pid": 2 ** 30, "what": "Reading what you wrote", "since": 0}})
        v = shape.view(self.home, "lamp")
        self.assertIsNone(v["busy"])
        self.assertIn("stopped", v["error"])

    def test_a_failed_review_runs_again_on_retry(self):
        self.write_code("mathx.py", "def double(n):\n    return n * 2\n")
        self.set_env(SECURITY_LEVEL="0", WORKFLOW="drafts")
        shape._save(self.home, self.name, {"phase": "building", "spec": SPEC, "security": 0})
        def down(*a, **k): raise ollama.ModelError("cannot reach the model server")
        shape.chat = down
        self.assertIn("did not finish", shape.run_review(self.home, self.name))
        self.assertEqual(shape.view(self.home, self.name)["phase"], "done")
        self.script([{"summary": "Nothing unused.", "findings": []}])
        shape.retry(self.home, self.name); self.wait(self.name)
        v = shape.view(self.home, self.name)
        self.assertEqual(len(self.calls), 1, "the retry must call the model again")
        self.assertIsNone(v["error"]); self.assertIn("Nothing unused.", v["notice"])

    def test_a_plan_without_examples_is_not_built(self):
        d = os.path.join(self.home, "projects", "vague"); os.makedirs(d)
        with open(os.path.join(d, "project.env"), "w") as f: f.write('WORKFLOW="shape"\n')
        shape._save(self.home, "vague", {"phase": "ready", "spec": "# Lamp\n\n## Requirements\n\n1. It glows.\n", "security": 1, "questions": []})
        with self.assertRaises(shape.ShapeError) as cm: shape.prepare_build(self.home, "vague", os.path.join(self.tmp, "lamp"))
        self.assertIn("no examples", str(cm.exception))
        self.assertEqual(read_env(os.path.join(d, "project.env"))["WORKFLOW"], "shape")

    def test_slug(self):
        self.assertEqual(shape.slug("I want a small egg timer for the kitchen"), "small-egg-timer")
