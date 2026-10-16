"""Replay corpus: the harness's handling of REAL model output, checked offline in seconds instead of discovered by a 5-hour run.
tests/corpus/assertions.json holds every distinct assertion a real planner/splitter froze (282 from 35 projects, 2026-10-15/16), each judged against the
spec's reference solution ("holds" = sound) and the bare seed; tests/corpus/plan_replies.json holds raw planner replies. Rebuild after a real run with
`python3 tests/corpus/build_corpus.py`. Two kinds of check:
  - invariants that must always hold (a rule must never reject or change a SOUND assertion; a known impossible class is always caught);
  - ratchets: counts of known bad outcomes that may only go DOWN. When a fix lowers one, lower the number here in the same commit."""
import json, os, shutil, subprocess, sys, unittest
from helpers import Sandboxed
import collections
from codebot import assertions as A, checkspec as K, planner

CORPUS = os.path.join(os.path.dirname(os.path.realpath(__file__)), "corpus")
HOME = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
REF = {"level_01": ["bench/ladder/catalog/level_01/seed", "bench/ladder/catalog/level_01/reference_solution"], "pyutils": ["bench/suite/pyutils/seed", "bench/suite/pyutils/reference"]}

def load(name):
    with open(os.path.join(CORPUS, name)) as f: return json.load(f)

def leaked(a): return a["type"] == "call" and any(isinstance(v, str) and v in A.LEAKED for v in a.get("args") or [])

# Ratchets (2026-10-16). Unsound = fails on the reference solution, so no correct code can ever pass it.
UNSOUND_ACCEPTED_MAX = 92           # unsound corpus assertions that clean()+validate() leave unchanged and accept (105 unsound in total; 5 are repaired today)
COLLAPSED_PLANS = 3                 # raw first-round replies with fewer tasks than the spec has items (all three before minItems = spec items)

def pipeline(rows):
    """What the harness freezes for each corpus assertion: clean(), then repair_plan() with the assertion's whole project as context (the same
    plan-level view planner._write has). Returns {row index: processed assertion} (an assertion seen in several projects is processed in the first)."""
    proj = collections.defaultdict(list)
    for i, r in enumerate(rows):
        for s in r["sources"]: proj[(r["suite"], s["project"])].append(i)
    out = {}
    for idx in proj.values():
        fixed, _ = K.repair_plan([[K.clean(rows[i]["assert"]) for i in idx]])
        for i, a in zip(idx, fixed[0]): out.setdefault(i, a)
    return out

class TestAssertionCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.rows = load("assertions.json"); cls.done = pipeline(cls.rows)
    def test_corpus_is_present_and_judged(self):
        self.assertGreaterEqual(len(self.rows), 250)
        self.assertTrue(all(r["reference"] in ("holds", "fails") and r["seed"] in ("holds", "fails") for r in self.rows))
    def test_no_rule_rejects_a_sound_assertion(self):
        bad = [r["assert"] for r in self.rows if r["reference"] == "holds" and A.validate([K.clean(r["assert"])])]
        self.assertEqual(bad, [], "a validation rule rejects assertions that the reference solution satisfies")
    def test_clean_and_repair_never_change_a_sound_assertion(self):
        changed = [r["assert"] for i, r in enumerate(self.rows) if r["reference"] == "holds" and self.done[i] != r["assert"]]
        self.assertEqual(changed, [], "clean()/repair_plan() rewrite assertions that were already right")
    def test_a_leaked_field_name_is_always_repaired_or_refused(self):
        for r in self.rows:
            if leaked(r["assert"]):
                c = K.clean(r["assert"]); self.assertTrue(not leaked(c) or A.validate([c]), r["assert"])
    def test_ratchet_unsound_assertions_let_through(self):
        through = [r for i, r in enumerate(self.rows) if r["reference"] == "fails" and self.done[i] == r["assert"] and A.validate([r["assert"]]) is None]   # repaired ones are re-judged below
        self.assertLessEqual(len(through), UNSOUND_ACCEPTED_MAX, f"{len(through)} unsound assertions pass clean()+validate() (ratchet {UNSOUND_ACCEPTED_MAX}): a change let more impossible checks through")
        if len(through) < UNSOUND_ACCEPTED_MAX: print(f"\n[corpus] unsound let through: {len(through)} < ratchet {UNSOUND_ACCEPTED_MAX}; lower UNSOUND_ACCEPTED_MAX", file=sys.stderr)
    def test_repaired_leaks_hold_on_the_reference(self):
        """A repair is only good if it turns the assertion into a sound one: re-judge every assertion clean() changed, on the reference solution."""
        changed = [(r["suite"], self.done[i]) for i, r in enumerate(self.rows) if self.done[i] != r["assert"] and A.validate([self.done[i]]) is None]     # repaired AND accepted
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
        self.replies = [r for r in load("plan_replies.json") if r["suite"] == "level_01" and r.get("kind", "plan") == "plan"]
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

    def test_the_plan_that_sank_run_gate4_now_freezes_no_impossible_check(self):
        # 2026-10-16 run 20261016-095343-l1-gate4: T1, T3 and T4 each froze a check like slugify("", "") == "" (the expected value copied into args),
        # so 6 of the 8 functions could never be verified. After repair + salvage every frozen call/run check must hold on the reference solution.
        r = next(x for x in self.replies if x["project"].endswith("095343-l1-gate4-l01-r1") and x["round"] == 1)
        plan = json.loads(r["raw"]); bad = planner._write(self.proj(), plan, self.spec); self.assertEqual(bad, {})
        frozen = [a for i in range(1, len(plan["tasks"]) + 1) for a in K.read_spec(self.proj(), f"T{i}") if a["type"] in ("call", "run")]
        self.assertGreater(len(frozen), 30)
        self.assertEqual([m for m in judge(frozen, REF["level_01"]) if m], [])

if __name__ == "__main__": unittest.main()
