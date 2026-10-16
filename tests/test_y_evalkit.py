"""Model-evaluation kit: mutation operators, the stored mutant sets, and the plan scorer (E/S/K/Q) against the level-1 reference solution.
The scorer runs here without Docker (SANDBOX=none) because every assertion in these tests is written by the test itself."""
import ast, json, os, shutil, unittest
from helpers import Sandboxed
from codebot.capbench import manifests as M
from codebot.evalkit import mutants as MU, planscore as PS, planeval as PE

HOME = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
SRC = "def f(x, y):\n    '''doc'''\n    if x < 3 and not y:\n        raise ValueError('bad')\n    return x + 1 == 2\n"

class TestMutants(unittest.TestCase):
    def test_every_site_gives_valid_different_code_and_docstrings_are_left_alone(self):
        tree = ast.parse(SRC); ss = MU.sites(tree)
        ops = sorted({op.split(":")[0] for _, op, _ in ss}); self.assertEqual(ops, ["bin", "bool", "cmp", "int+1", "int-1", "noraise", "not", "str"])
        outs = {ast.unparse(MU.apply(tree, i, op)) for i, op, _ in ss}
        self.assertEqual(len(outs), len(ss)); self.assertNotIn(ast.unparse(tree), outs)
        self.assertTrue(all("doc" in o for o in outs))                                       # the docstring is never mutated (a short string would become '#')
        for o in outs: compile(o, "m", "exec")
    def test_stored_sets_match_their_reference_solution(self):
        for lv in ("level_01", "level_02", "level_03"):
            m = M.load(HOME, lv); d = MU.load(m); self.assertIsNotNone(d, f"{lv}: mutants.json missing or stale (run codebot eval mutants)")
            self.assertGreater(len(d["mutants"]), 20); self.assertTrue(all(x["requirements"] for x in d["mutants"]))
    def test_a_changed_reference_makes_the_set_stale(self):
        m = M.load(HOME, "level_01"); real = MU.solution_hash
        MU.solution_hash = lambda m: "other"
        try: self.assertIsNone(MU.load(m))
        finally: MU.solution_hash = real

class TestPlanScore(Sandboxed):
    @classmethod
    def setUpClass(cls):
        cls.m = M.load(HOME, "level_01"); cls.spec = M.spec_text(cls.m)
        with open(M.plan_path(cls.m)) as f: cls.ref = [t["asserts"] for t in json.load(f)["tasks"]]
    def setUp(self): super().setUp(); self.set_env(SANDBOX="none"); os.makedirs(os.path.join(self.home, "bench", "runs"), exist_ok=True)
    def score(self, tasks): return PS.score(self.proj(), self.m, tasks, self.spec)
    def test_the_reference_plan_is_fully_executable_and_sound(self):
        r = self.score(self.ref)
        self.assertEqual((r["E"], r["S"], r["unsound_by_kind"]), (1.0, 1.0, {})); self.assertEqual(r["requirements_covered"], 8)
        self.assertTrue(0.5 < r["K"] < 1.0, r["K"]); self.assertEqual(r["Q"], r["K"])
    def test_one_impossible_assertion_dooms_its_task_and_is_classified(self):
        bad = [list(a) for a in self.ref]
        bad[0] = bad[0] + [{"type": "call", "module": "textutil", "func": "slugify", "args": ["", ""], "expect": ""}]
        bad[2] = bad[2] + [{"type": "call", "module": "numutil", "func": "clamp", "args": [1, 2, 3], "raises": "ValueError"}]
        r = self.score(bad)
        self.assertEqual(r["E"], 0.75); self.assertLess(r["S"], 1.0)
        self.assertEqual(r["unsound_by_kind"], {"signature": 1, "expects an exception the code does not raise": 1})
        self.assertEqual(r["per_task"][0]["unsound"][0][:7], "slugify")
    def test_coverage_ignores_unsound_assertions_and_rewards_more_tests(self):
        few = [[a[0]] for a in self.ref]                                          # one assertion per task
        wrong_everywhere = [[{"type": "call", "module": "textutil", "func": "slugify", "args": ["x"], "expect": "WRONG"}]] + [[] for _ in self.ref[1:]]
        r_few, r_all, r_wrong = self.score(few), self.score(self.ref), self.score(wrong_everywhere)
        self.assertLess(r_few["K"], r_all["K"])                                  # two trivial correct tests do not outrank a thorough plan
        self.assertEqual(r_wrong["K"], 0.0); self.assertEqual(r_wrong["E"], 0.0)  # an assertion that fails everywhere "catches" nothing
    def test_model_view_counts_unreadable_assertions_as_malformed(self):
        tasks, bad = PS.model_view([{"asserts": [{"type": "call", "module": "m", "func": "f", "args": [], "expect": 1}, {"type": "shell", "cmd": "ls"}]}, None])
        self.assertEqual((len(tasks[0]), tasks[1], bad), (1, [], 1))

class TestSummary(unittest.TestCase):
    def test_summary_groups_configurations(self):
        row = lambda th, e: {"level": "level_01", "model": "m", "think": th, "ok": True, "secs": 600, "cost": {"gen_tokens": 100}, "malformed_model_asserts": 0,
                             "frozen": {"E": e, "S": 0.9, "K": 0.8, "Q": e * 0.8, "asserts": 60}, "model_view": {"E": e, "S": 0.9, "K": 0.8, "Q": e * 0.8}}
        md = PE.summary([row("off", 0.5), row("off", 0.7), row("on", 0.9)])
        self.assertIn("| level_01 | `m` | off | 2 | 2 | 0.60 ± 0.14", md); self.assertIn("| level_01 | `m` | on | 1 | 1 | 0.90", md)

if __name__ == "__main__": unittest.main()
