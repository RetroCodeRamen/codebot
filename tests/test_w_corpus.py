"""Replay corpus: the harness's handling of REAL model output, checked offline in seconds instead of discovered by a 5-hour run.
tests/corpus/assertions.json holds every distinct assertion a real planner/splitter froze (282 from 35 projects, 2026-10-15/16), each judged against the
spec's reference solution ("holds" = sound) and the bare seed; tests/corpus/plan_replies.json holds raw planner replies. Rebuild after a real run with
`python3 tests/corpus/build_corpus.py`. Two kinds of check:
  - invariants that must always hold (a rule must never reject or change a SOUND assertion; a known impossible class is always caught);
  - ratchets: counts of known bad outcomes that may only go DOWN. When a fix lowers one, lower the number here in the same commit."""
import json, os, shutil, subprocess, sys, unittest
from helpers import Sandboxed
import collections
from codebot import assertions as A, checkspec as K, grounding as G, planner

CORPUS = os.path.join(os.path.dirname(os.path.realpath(__file__)), "corpus")
HOME = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
REF = {"level_01": ["bench/ladder/catalog/level_01/seed", "bench/ladder/catalog/level_01/reference_solution"], "pyutils": ["bench/suite/pyutils/seed", "bench/suite/pyutils/reference"],
       "calc_go": ["bench/ladder/catalog/calc_go/seed", "bench/ladder/catalog/calc_go/reference_solution"]}
SPEC = {"level_01": "bench/ladder/catalog/level_01/SPEC.md", "pyutils": "bench/suite/pyutils/spec.md", "calc_go": "bench/ladder/catalog/calc_go/SPEC.md"}
sys.path.insert(0, CORPUS)
import build_corpus as BC                                     # noqa: E402  (its prepared_env builds the Go program before judging)

def load(name):
    with open(os.path.join(CORPUS, name)) as f: return json.load(f)

def leaked(a): return a["type"] == "call" and any(isinstance(v, str) and v in A.LEAKED for v in a.get("args") or [])

# Ratchets (2026-10-16). Unsound = fails on the reference solution, so no correct code can ever pass it.
UNSOUND_ACCEPTED_MAX = {"level_01": 104, "pyutils": 78, "calc_go": 186}      # unsound assertions that clean()+validate() leave unchanged and accept, per suite
                                    # (2026-10-17 rebuild: 381 unsound of 718; the plan-level screen, grounding.screen, catches far more: see TestScreenCorpus)
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
        through = collections.Counter(r["suite"] for i, r in enumerate(self.rows) if r["reference"] == "fails" and self.done[i] == r["assert"] and A.validate([r["assert"]]) is None)   # repaired ones are re-judged below
        for suite, n in UNSOUND_ACCEPTED_MAX.items():
            self.assertLessEqual(through[suite], n, f"{suite}: {through[suite]} unsound assertions pass clean()+validate() (ratchet {n}): a change let more impossible checks through")
            if through[suite] < n: print(f"\n[corpus] {suite}: unsound let through {through[suite]} < ratchet {n}; lower UNSOUND_ACCEPTED_MAX", file=sys.stderr)
    def test_repaired_leaks_hold_on_the_reference(self):
        """A repair is only good if it turns the assertion into a sound one: re-judge every assertion clean() changed, on the reference solution."""
        changed = [(r["suite"], self.done[i]) for i, r in enumerate(self.rows) if self.done[i] != r["assert"] and A.validate([self.done[i]]) is None]     # repaired AND accepted
        for suite in {s for s, _ in changed}:
            got = judge([c for s, c in changed if s == suite], REF[suite], suite)
            fails = [(c, m) for (s, c), m in zip([x for x in changed if x[0] == suite], got) if m]
            self.assertEqual(fails, [], "a repair produced an assertion the reference solution does not satisfy")

def judge(asserts, dirs, suite=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for src in dirs: shutil.copytree(os.path.join(HOME, src), d, dirs_exist_ok=True)
        env = BC.prepared_env(suite, d)
        with open(os.path.join(d, "_a.json"), "w") as f: json.dump(asserts, f)
        code = ("import json, sys; sys.path.insert(0, %r); from codebot import assertions as A\n"
                "print(json.dumps([(A.check([a]) or [None])[0] for a in json.load(open('_a.json'))]))") % os.path.join(HOME, "lib")
        return json.loads(subprocess.run([sys.executable, "-c", code], cwd=d, capture_output=True, text=True, timeout=600, env=env, stdin=subprocess.DEVNULL).stdout.strip().splitlines()[-1])

def screened(rows):
    """Every corpus assertion through grounding.screen the way the harness applies it: each project's planned tasks as one plan (against every spec
    example), then its split children with the screened plan as context. Returns {(suite, project, task, assertion key): kept?}."""
    plans = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        for s in r["sources"]: plans[(r["suite"], s["project"])][s["task"]].append(r["assert"])
    out = {}
    for (suite, project), tasks in plans.items():
        with open(os.path.join(HOME, SPEC[suite])) as f: spec = f.read()
        kind, ex = G.project_kind(spec, os.path.join(HOME, REF[suite][0])), G.all_examples(spec)
        ids = sorted(tasks, key=lambda t: (t.count("."), t)); fixed = dict(zip(ids, K.repair_plan([[K.clean(a) for a in tasks[t]] for t in ids])[0]))
        top = [t for t in ids if "." not in t]; kids = [t for t in ids if "." in t]
        o1 = G.screen([fixed[t] for t in top], spec, kind, None, ex)[0]
        o2 = G.screen([fixed[t] for t in kids], spec, kind, None, ex, [a for g in o1 for a in g])[0]
        for ts, outs in ((top, o1), (kids, o2)):
            for t, g in zip(ts, outs):
                keep = {K.akey(a) for a in g}
                for orig, a in zip(tasks[t], fixed[t]): out[(suite, project, t, K.akey(orig))] = K.akey(a) in keep
    return out

# Ratchets for grounding.screen (2026-10-17), counted per use of an assertion. A correct assertion (holds on the reference) is dropped only where
# its own task contradicts it and nothing breaks the tie (level_01: is_prime(True), is_prime(3.5) and clamp(10, 10, 10) asserted both to return a value
# and to raise; pyutils: parse_kv the same), and in calc_go four `python3 calc.py ...` checks that "hold" only because Python cannot open the missing
# file. Impossible assertions the screen lets through are mostly wrong expected values no spec example covers (the re-derivation handles those).
SCREEN_SOUND_DROPPED = {"level_01": 3, "pyutils": 1, "calc_go": 4}
SCREEN_UNSOUND_KEPT = {"level_01": 177, "pyutils": 106, "calc_go": 31}     # may only go down

class TestScreenCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.rows = load("assertions.json"); cls.kept = screened(cls.rows)
    def count(self, suite, verdict, kept):
        return sum(1 for r in self.rows if r["suite"] == suite and r["reference"] == verdict for s in r["sources"] if self.kept[(suite, s["project"], s["task"], K.akey(r["assert"]))] == kept)
    def test_correct_assertions_are_kept(self):
        for suite, n in SCREEN_SOUND_DROPPED.items(): self.assertLessEqual(self.count(suite, "holds", False), n, f"{suite}: the screen drops correct assertions")
    def test_ratchet_impossible_assertions_let_through(self):
        for suite, n in SCREEN_UNSOUND_KEPT.items():
            got = self.count(suite, "fails", True); self.assertLessEqual(got, n, f"{suite}: the screen lets more impossible assertions through")
            if got < n: print(f"\n[corpus] {suite}: screen lets {got} impossible assertions through < ratchet {n}; lower SCREEN_UNSOUND_KEPT", file=sys.stderr)
    def test_the_go_calculator_control_rerun(self):
        # 2026-10-17: 38 of the 41 checks this run froze could not pass for a correct calculator; the screen must remove most of them, keep the rest
        p = "cap-20261017-131051-calc2-control-rerun-calc-go-r1"
        rows = [(r, s) for r in self.rows if r["suite"] == "calc_go" for s in r["sources"] if s["project"] == p]
        self.assertGreater(len(rows), 100)
        bad = [r for r, s in rows if r["reference"] == "fails"]; dropped = [r for r, s in rows if r["reference"] == "fails" and not self.kept[("calc_go", p, s["task"], K.akey(r["assert"]))]]
        self.assertGreater(len(dropped), 0.9 * len(bad)); self.assertEqual([r["assert"] for r, s in rows if r["reference"] == "holds" and not self.kept[("calc_go", p, s["task"], K.akey(r["assert"]))]], [])

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
        short = [r for r in self.replies if "goal" in json.loads(r["raw"]) and len(json.loads(r["raw"])["tasks"]) < want]     # whole plans only, not repair replies
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
