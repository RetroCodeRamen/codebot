"""Replay corpus: the harness's handling of REAL model output, checked offline in seconds instead of discovered by a 5-hour run.
tests/corpus/assertions.json holds every distinct assertion a real planner/splitter froze (282 from 35 projects, 2026-10-15/16), each judged against the
spec's reference solution ("holds" = sound) and the bare seed; tests/corpus/plan_replies.json holds raw planner replies. Rebuild after a real run with
`python3 tests/corpus/build_corpus.py`. Two kinds of check:
  - invariants that must always hold (a rule must never reject or change a SOUND assertion; a known impossible class is always caught);
  - ratchets: counts of known bad outcomes that may only go DOWN. When a fix lowers one, lower the number here in the same commit."""
import json, os, shutil, subprocess, sys, unittest
from helpers import Sandboxed
from codebot import assertions as A, checkspec as K, planner

CORPUS = os.path.join(os.path.dirname(os.path.realpath(__file__)), "corpus")
HOME = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
REF = {"level_01": ["bench/ladder/catalog/level_01/seed", "bench/ladder/catalog/level_01/reference_solution"], "pyutils": ["bench/suite/pyutils/seed", "bench/suite/pyutils/reference"]}

def load(name):
    with open(os.path.join(CORPUS, name)) as f: return json.load(f)

def leaked(a): return a["type"] == "call" and any(isinstance(v, str) and v in A.LEAKED for v in a.get("args") or [])

# Ratchets (2026-10-16). Unsound = fails on the reference solution, so no correct code can ever pass it.
UNSOUND_ACCEPTED_MAX = 100          # unsound corpus assertions that clean()+validate() leave unchanged and accept (105 unsound in total; 5 are repaired today)
COLLAPSED_PLANS = 3                 # raw first-round replies with fewer tasks than the spec has items (all three before minItems = spec items)

class TestAssertionCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.rows = load("assertions.json")
    def test_corpus_is_present_and_judged(self):
        self.assertGreaterEqual(len(self.rows), 250)
        self.assertTrue(all(r["reference"] in ("holds", "fails") and r["seed"] in ("holds", "fails") for r in self.rows))
    def test_no_rule_rejects_a_sound_assertion(self):
        bad = [r["assert"] for r in self.rows if r["reference"] == "holds" and A.validate([K.clean(r["assert"])])]
        self.assertEqual(bad, [], "a validation rule rejects assertions that the reference solution satisfies")
    def test_clean_never_changes_a_sound_assertion(self):
        changed = [r["assert"] for r in self.rows if r["reference"] == "holds" and K.clean(r["assert"]) != {k: v for k, v in r["assert"].items()}]
        self.assertEqual(changed, [], "clean() rewrites assertions that were already right")
    def test_a_leaked_field_name_is_always_repaired_or_refused(self):
        for r in self.rows:
            if leaked(r["assert"]):
                c = K.clean(r["assert"]); self.assertTrue(not leaked(c) or A.validate([c]), r["assert"])
    def test_ratchet_unsound_assertions_let_through(self):
        through = [r for r in self.rows if r["reference"] == "fails" and K.clean(r["assert"]) == r["assert"] and A.validate([r["assert"]]) is None]   # repaired ones are re-judged below
        self.assertLessEqual(len(through), UNSOUND_ACCEPTED_MAX, f"{len(through)} unsound assertions pass clean()+validate() (ratchet {UNSOUND_ACCEPTED_MAX}): a change let more impossible checks through")
        if len(through) < UNSOUND_ACCEPTED_MAX: print(f"\n[corpus] unsound let through: {len(through)} < ratchet {UNSOUND_ACCEPTED_MAX}; lower UNSOUND_ACCEPTED_MAX", file=sys.stderr)
    def test_repaired_leaks_hold_on_the_reference(self):
        """A repair is only good if it turns the assertion into a sound one: re-judge every assertion clean() changed, on the reference solution."""
        changed = [(r["suite"], K.clean(r["assert"])) for r in self.rows if K.clean(r["assert"]) != r["assert"] and A.validate([K.clean(r["assert"])]) is None]     # repaired AND accepted
        for suite in {s for s, _ in changed}:
            got = judge([c for s, c in changed if s == suite], REF[suite])
            fails = [(c, m) for (s, c), m in zip([x for x in changed if x[0] == suite], got) if m]
            self.assertEqual(fails, [], "a repair produced an assertion the reference solution does not satisfy")

def judge(asserts, dirs):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for src in dirs: shutil.copytree(os.path.join(HOME, src), d, dirs_exist_ok=True)
        with open(os.path.join(d, "_a.json"), "w") as f: json.dump(asserts, f)
        code = ("import json, sys; sys.path.insert(0, %r); from codebot import assertions as A\n"
                "print(json.dumps([(A.check([a]) or [None])[0] for a in json.load(open('_a.json'))]))") % os.path.join(HOME, "lib")
        return json.loads(subprocess.run([sys.executable, "-c", code], cwd=d, capture_output=True, text=True, timeout=300).stdout.strip().splitlines()[-1])

class TestPlanReplyReplay(Sandboxed):
    """Each raw planner reply goes through the harness's post-processing exactly as plan_auto would (no model), on the level-1 seed."""
    def setUp(self):
        super().setUp(); self.set_env(CHECK_BASELINE_GUARD="1", SANDBOX="none")
        shutil.copytree(os.path.join(HOME, REF["level_01"][0]), self.code, dirs_exist_ok=True)
        with open(os.path.join(HOME, "bench/ladder/catalog/level_01/SPEC.md")) as f: self.spec = f.read()
        with open(os.path.join(self.state, "SPEC.md"), "w") as f: f.write(self.spec)
        self.replies = [r for r in load("plan_replies.json") if r["suite"] == "level_01"]
    def test_collapsed_plans_are_counted(self):
        want = planner.spec_items(self.spec); self.assertEqual(want, 8)
        short = [r for r in self.replies if len(json.loads(r["raw"])["tasks"]) < want]
        self.assertEqual(len(short), COLLAPSED_PLANS)
        self.assertEqual(planner.plan_schema(10, want)["properties"]["tasks"]["minItems"], 8)      # the structural fix: constrained decoding cannot produce these any more
    def test_full_plans_are_written_and_guarded_without_crashing(self):
        for r in self.replies:
            plan = json.loads(r["raw"])
            if len(plan["tasks"]) < 8: continue
            bad = planner._write(self.proj(), plan, self.spec); rej = planner._evaluate(self.proj(), bad)
            print(f"\n[corpus] {r['project'][-26:]} round {r['round']}: {len(plan['tasks'])} tasks, {len(rej)} rejected {[x['id'] + ':' + x['verdict'] for x in rej]}", file=sys.stderr)
            self.assertLessEqual(len(rej), len(plan["tasks"]))

if __name__ == "__main__": unittest.main()
