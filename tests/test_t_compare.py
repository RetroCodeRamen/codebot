"""Side-by-side model comparison plumbing: --model is stored in the project (planner, worker and splitter all read it) and --compare tabulates stored results."""
import json, os, shutil, tempfile, unittest
from helpers import Sandboxed
from codebot import bench

def result(model, done, secs):
    return {"run": {"status": "finished", "minutes": 7.5, "temps": [80.0, 90.0], "model": model},
            "plan": "x", "plan_data": {"rounds": [{"n": 1}], "secs": secs, "ok": True},
            "result": {"pass_rate": {"done": done, "total": 5, "rate": done / 5}, "attempts_per_task": 1.2, "thermal_pauses": 1, "thermal_wait_s": 20,
                       "turns": {"turns": 30, "gen_tok_per_s": 9.0, "cold_prompt_tok_per_s": 340.0, "cache_reuse_later_turns": 0.8, "malformed": 0, "schema_failure_rate": 0.0, "truncated": 0},
                       "guard": {"rejected_total": 0}, "ladder": {"escalations": [], "splits": []}, "tasks": [{"status": "done"}] * done + [{"status": "blocked"}] * (5 - done)}}

class TestCompare(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="cbtest-"); os.makedirs(os.path.join(self.home, "bench", "results"))
        for n, m, d, s in (("a", "codebot-model", 5, 178), ("b", "qwen-coder-model", 3, 90)):
            with open(os.path.join(self.home, "bench", "results", f"{n}.json"), "w") as f: json.dump(result(m, d, s), f)
    def tearDown(self): shutil.rmtree(self.home, ignore_errors=True)
    def test_table_has_one_column_per_run_and_the_key_rows(self):
        t = bench.compare(["a", "b"], self.home); head = t.splitlines()[0]
        self.assertIn("`a`", head); self.assertIn("`b`", head)
        for frag in ("| model | codebot-model | qwen-coder-model |", "| tasks verified | 5/5 | 3/5 |", "| blocked | 0 | 2 |", "| planner | 1 round(s), 178 s, ok=True | 1 round(s), 90 s, ok=True |"): self.assertIn(frag, t)
    def test_missing_result_is_a_clear_error(self):
        with self.assertRaises(SystemExit) as e: bench.compare(["a", "nope"], self.home)
        self.assertIn("no stored result for nope", str(e.exception))

class TestModelIsStoredInTheProject(Sandboxed):
    def test_setup_writes_the_model_into_project_env(self):
        suite = os.path.join(self.home, "bench", "suite", "s"); os.makedirs(os.path.join(suite, "seed")); open(os.path.join(suite, "seed", "a.py"), "w").write("x = 1\n"); open(os.path.join(suite, "spec.md"), "w").write("spec\n")
        name = bench.setup(self.home, "s", "auto", "m1", model="qwen-coder-model"); self.assertEqual(self.proj_of(name).get("CODEBOT_MODEL"), "qwen-coder-model")
        name2 = bench.setup(self.home, "s", "auto", "m2"); self.assertEqual(self.proj_of(name2).get("CODEBOT_MODEL", "codebot-model"), "codebot-model")
    def proj_of(self, n):
        from codebot.config import Project; return Project(n, self.home)

class TestTokenRatioIsPerModel(Sandboxed):
    def put(self, name, cpt):
        os.makedirs(os.path.join(self.home, "logs"), exist_ok=True); json.dump({"chars_per_token": cpt}, open(os.path.join(self.home, "logs", name), "w"))
    def test_each_model_uses_its_own_calibration(self):
        from codebot import tokens
        self.put("tokenizer_ratio.json", 3.0); self.put("tokenizer_ratio.qwen-coder-model.json", 3.5)
        self.assertAlmostEqual(tokens.ratio(self.proj()), 3.0 * 0.92)                           # the default model keeps the original file
        self.set_env(CODEBOT_MODEL="qwen-coder-model"); self.assertAlmostEqual(tokens.ratio(self.proj()), 3.5 * 0.92)
    def test_another_model_never_borrows_the_default_models_file(self):
        from codebot import tokens
        self.put("tokenizer_ratio.json", 3.9); self.set_env(CODEBOT_MODEL="other-model"); self.assertEqual(tokens.ratio(self.proj()), tokens.DEFAULT_RATIO)

if __name__ == "__main__": unittest.main()
