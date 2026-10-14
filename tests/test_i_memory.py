"""F9: append-only raw lessons, provenance, outcome correlation, pruning, generated + capped lessons.md."""
import hashlib, json, os, unittest
from helpers import Sandboxed
from codebot import engine, memory as M, packer, tasks as T

PLAN = """
# Tasks
- [ ] 1. parse the config file reader
  - check: `grep -qx ok a.txt`
  - files: config_reader.py
"""
class Base(Sandboxed):
    def setUp(self): super().setUp(); self.write_plan(PLAN); engine.freeze_project(self.proj())
    def raw_path(self): return os.path.join(self.home, "memory", "lessons.raw.jsonl")
    def md(self):
        with open(os.path.join(self.home, "memory", "lessons.md")) as f: return f.read()
    def status(self):
        with open(os.path.join(self.home, "memory", "lessons.status.json")) as f: return json.load(f)
    def task(self): return T.TaskStore(self.proj()).load()["tasks"][0]

class TestRawLog(Base):
    def test_add_appends_and_dedupes(self):
        a = M.add(self.home, "always close file handles in the config reader", "t1", "T1", 1)
        self.assertIsNotNone(a); self.assertIsNone(M.add(self.home, "Always close file handles in the  config reader", "t2", "T9"))
        self.assertIsNone(M.add(self.home, "short", "t1", "T1"))
        self.assertEqual(len(open(self.raw_path()).read().splitlines()), 1)
        srcs = self.status()["lessons"][a]["sources"]; self.assertEqual({(s["project"], s["task"]) for s in srcs}, {("t1", "T1"), ("t2", "T9")})   # provenance kept

    def test_raw_is_never_rewritten(self):
        M.add(self.home, "first lesson about parsing config files", "t1", "T1")
        before = open(self.raw_path(), "rb").read()
        M.add(self.home, "second lesson about reading files safely", "t1", "T2")
        for _ in range(12): M.record_outcome(self.home, [M.add(self.home, "third lesson about encodings in readers", "t1", "T3") or "x"], False)
        M.prune(self.home); M.rebuild_md(self.home)
        self.assertTrue(open(self.raw_path(), "rb").read().startswith(before))

class TestSelectAndOutcomes(Base):
    def test_select_ranks_by_relevance_and_respects_limits(self):
        for t in ("config reader must handle empty files", "unrelated advice about docker networking", "reader should close handles in config parsing"):
            M.add(self.home, t, "t1", "T1")
        got = M.select(self.proj(), self.task(), k=2)
        self.assertEqual(len(got), 2); self.assertTrue(all("reader" in g["text"] for g in got)); self.assertTrue(all(len(g["id"]) == 8 for g in got))
        self.assertEqual(M.select(self.proj(), self.task(), k=5, cap=10), [])          # cap respected

    def test_outcomes_update_counts_and_baseline(self):
        a = M.add(self.home, "config reader lesson number one here", "t1", "T1")
        M.record_outcome(self.home, [a], True); M.record_outcome(self.home, [a], False); M.record_outcome(self.home, [], True)
        s = self.status(); self.assertEqual((s["lessons"][a]["used"], s["lessons"][a]["pass"]), (2, 1)); self.assertEqual(s["baseline"], {"n": 3, "pass": 2})

class TestPruning(Base):
    def seed(self, bad_used=10, bad_pass=1, good_used=10, good_pass=9, base_extra=40):
        bad = M.add(self.home, "reader lesson that correlates with failure", "t1", "T1"); good = M.add(self.home, "reader lesson that correlates with success", "t1", "T1")
        for i in range(bad_used): M.record_outcome(self.home, [bad], i < bad_pass)
        for i in range(good_used): M.record_outcome(self.home, [good], i < good_pass)
        for i in range(base_extra): M.record_outcome(self.home, [], i % 10 < 8)             # ordinary attempts: ~80% pass
        return bad, good

    def test_failure_correlated_lesson_is_pruned_good_one_kept(self):
        bad, good = self.seed(); self.assertEqual(M.prune(self.home), [bad])
        self.assertTrue(self.status()["lessons"][bad]["pruned"]); self.assertIn("vs baseline", self.status()["lessons"][bad]["pruned_reason"])
        self.assertNotIn("correlates with failure", self.md()); self.assertIn("correlates with success", self.md())
        self.assertIn(bad, M.load_raw(self.home))                                          # pruned = flagged, never deleted from raw
        self.assertEqual([l["id"] for l in M.select(self.proj(), self.task())], [good])

    def test_not_enough_samples_means_no_pruning(self):
        bad, _ = self.seed(bad_used=4, bad_pass=0); self.assertEqual(M.prune(self.home), [])

    def test_not_enough_baseline_means_no_pruning(self):
        self.seed(base_extra=0); self.assertEqual(M.prune(self.home, min_baseline=200), [])

    def test_margin_protects_slightly_unlucky_lessons(self):
        self.seed(bad_used=10, bad_pass=7); self.assertEqual(M.prune(self.home), [])

class TestGeneratedMarkdown(Base):
    def test_cap_and_order(self):
        for i in range(30): M.add(self.home, f"lesson number {i} about reading config files carefully{'!' * i}", "t1", "T1")
        M.rebuild_md(self.home, cap=600); md = self.md(); self.assertLessEqual(len(md), 600); self.assertTrue(md.startswith("# Lessons")); self.assertGreater(md.count("\n- "), 3)

    def test_good_evidence_wins_the_limited_space(self):
        a = M.add(self.home, "proven lesson about reader files", "t1", "T1"); b = M.add(self.home, "unproven lesson about reader files", "t1", "T1")
        for _ in range(8): M.record_outcome(self.home, [a], True)
        M.rebuild_md(self.home, cap=len("# Lessons (generated from memory/lessons.raw.jsonl; add new ones with the note_lesson action, do not edit this file)") + 60)
        self.assertIn("proven lesson", self.md()); self.assertNotIn("unproven", self.md())

class TestEngineIntegration(Base):
    def test_attempt_outcomes_are_attributed_to_packed_lessons(self):
        lid = M.add(self.home, "config reader lesson used during the attempt", "t1", "T1")
        w = self.worker(f"import os, json\njson.dump({{'task':'T1','lessons':['{lid}']}}, open(os.path.join(os.environ['CODEBOT_STATE'],'packed.json'),'w'))")
        self.set_env(WORKER_CMD=w, STUCK_SAME_FAILURE="99", STUCK_NO_PROGRESS="99")
        engine.step(self.proj()); engine.step(self.proj())
        e = self.status()["lessons"][lid]; self.assertEqual((e["used"], e["pass"]), (2, 0))
        self.assertEqual(self.status()["baseline"]["n"], 2)
        recs = [json.loads(l) for l in open(os.path.join(self.state, "attempts.jsonl")) if '"attempt"' in l]; self.assertEqual(recs[0]["lessons"], [lid])

    def test_stale_packed_json_is_not_reused(self):
        with open(os.path.join(self.state, "packed.json"), "w") as f: json.dump({"lessons": ["deadbeef"]}, f)
        self.set_env(WORKER_CMD=self.worker("pass")); engine.step(self.proj())
        self.assertNotIn("deadbeef", self.status()["lessons"])

    def test_packer_includes_selected_lessons_and_lists_ids(self):
        lid = M.add(self.home, "config reader should treat empty file as empty config", "t1", "T1")
        text, man = packer.pack(self.proj(), self.task())
        self.assertIn("treat empty file", text); self.assertEqual(man["lessons"], [lid])

    def test_summary(self):
        self.seed_attempts = [M.record_outcome(self.home, [], True) for _ in range(3)]
        s = M.summary(self.home); self.assertEqual((s["attempts_seen"], s["baseline_pass"]), (3, 1.0))

if __name__ == "__main__": unittest.main()
