"""F12: one summary of an unattended run, from the harness's own records."""
import json, os, subprocess, sys, time, unittest
from helpers import Sandboxed
from codebot import engine, report, memory, planops, tasks as T

PLAN = """
# Goal
x
# Tasks
- [ ] 1. make a.txt say hi
  - check: `cat a.txt 2>&1; grep -qx hi a.txt`
  - files: a.txt
- [ ] 2. make b.txt say yo
  - check: `cat b.txt 2>&1; grep -qx yo b.txt`
  - files: b.txt
  - deps: 1
- [ ] 3. make c.txt say ok
  - check: `cat c.txt 2>&1; grep -qx ok c.txt`
  - files: c.txt
"""
# T1 fixes itself but also drops a requirements file (outside its hint) and deletes README.md lines; T2 never works; T3 fine
WORKER = """
import os
t = os.environ['CODEBOT_TASK_ID']
if t == 'T1':
    open('a.txt', 'w').write('hi\\n'); open('requirements.txt', 'w').write('left-pad==1\\n'); open('README.md', 'w').write('')
if t == 'T3': open('c.txt', 'w').write('ok\\n')
"""
class Base(Sandboxed):
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.code, "README.md"), "w") as f: f.write("\n".join(f"line {i}" for i in range(80)) + "\n")
        os.system(f"git -C {self.code} add -A && git -C {self.code} -c user.email=t@t -c user.name=t commit -qm readme")
        self.write_plan(PLAN); engine.freeze_project(self.proj())
        self.set_env(WORKER_CMD=self.worker(WORKER), TASK_MAX_ATTEMPTS="3", STUCK_SAME_FAILURE="99", STUCK_NO_PROGRESS="99", PINGPONG_WINDOW="0", LADDER_TRIES="9,9,0,9,9")
        with open(os.path.join(self.state, "started.at"), "w") as f: f.write(str(int(time.time()) - 3 * 3600 - 20 * 60))
        for _ in range(8):
            if engine.step(self.proj())["event"] in ("done_with_blocked", "stalled"): break
        self.r = report.build(self.proj())

class TestContent(Base):
    def test_counts_and_sections(self):
        self.assertEqual(self.r["counts"], {"total": 3, "done": 2, "blocked": 1, "pending": 0})
        self.assertEqual([d["id"] for d in self.r["done"]], ["T1", "T3"]); self.assertEqual(self.r["blocked"][0]["id"], "T2")

    def test_blocked_question_is_exact_and_actionable(self):
        q = self.r["blocked"][0]["question"]; self.assertIn("make b.txt say yo", q); self.assertIn("codebot answer t1", q)
        md = report.markdown(self.r); self.assertIn("## Needs you", md); self.assertIn(q, md)

    def test_risky_diff_is_flagged_with_reasons(self):
        top = self.r["review"][0]; self.assertEqual(top["task"], "T1")
        why = " ".join(top["why"]); self.assertIn("outside the task's hint", why); self.assertIn("requirements.txt", why); self.assertIn("mostly deletions", why)
        self.assertEqual([x["task"] for x in self.r["review"]], ["T1", "T3"])                         # risky one ranks first

    def test_time_attempts_and_tokens(self):
        self.assertAlmostEqual(self.r["elapsed"] / 3600, 3.33, delta=0.05); self.assertEqual(self.r["attempts"], 5)      # 1 + 3 + 1
        self.assertIn("3h 2", report._fmt_dur(self.r["elapsed"]) + " "); self.assertIn("1", self.r["attempts_by_rung"])

    def test_lessons_added_and_pruned_since_the_run_started(self):
        old = memory.add(self.home, "an old lesson from before the run", "t1", "T0")
        time.sleep(1.1)
        with open(os.path.join(self.state, "started.at"), "w") as f: f.write(str(int(time.time())))      # the run "starts" after the old lesson
        time.sleep(1.1); new = memory.add(self.home, "reader files should be closed after parsing", "t1", "T1")
        bad = memory.add(self.home, "a lesson that will be judged harmful", "t1", "T1")
        for i in range(10): memory.record_outcome(self.home, [bad], False)
        for i in range(30): memory.record_outcome(self.home, [], True)
        memory.prune(self.home); r = report.build(self.proj())
        self.assertEqual({l["id"] for l in r["lessons_added"]}, {new, bad}); self.assertNotIn(old, [l["id"] for l in r["lessons_added"]])
        self.assertEqual([p["id"] for p in r["lessons_pruned"]], [bad])

    def test_replans_and_weak_checks(self):
        planops.apply(self.proj(), {"op": "add_task", "title": "extra", "check": "true", "reason": "r"}, "model")
        planops.apply(self.proj(), {"op": "delete_task", "task": "T3", "reason": "r"}, "model")
        engine.step(self.proj()); r = report.build(self.proj())            # the new task's check passes before any work
        self.assertEqual((r["replans"]["accepted"], r["replans"]["rejected"]), (1, 1)); self.assertTrue(r["weak_checks"])
        self.assertIn("weak checks", report.markdown(r))

    def test_tamper_is_counted(self):
        self.set_env(WORKER_CMD=self.worker("import os\nopen(os.path.join(os.environ['CODEBOT_CHECKS'],'T2.sh'),'w').write('exit 0')"))
        engine.answer(self.proj(), "T2: try again"); engine.step(self.proj()); self.assertEqual(report.build(self.proj())["tamper"], 1)

    def test_safety_section(self):
        md = report.markdown(self.r); self.assertIn("## Safety", md); self.assertIn("audit chain", md)

class TestOutputs(Base):
    def test_short_text_fits_a_push_and_leads_with_what_matters(self):
        s = report.short(self.r); self.assertLess(len(s), 1200)
        self.assertTrue(s.startswith("2/3 tasks verified")); self.assertIn("1 blocked and waiting on you", s); self.assertIn("BLOCKED T2:", s); self.assertIn("Review first: T1", s)

    def test_write_creates_a_dated_file(self):
        path = report.write(self.proj(), self.r); self.assertTrue(os.path.basename(path).startswith("report-")); self.assertIn("morning report: t1", open(path).read())

    def test_cli_modes(self):
        eng = os.path.join(os.path.dirname(__file__), "..", "bin", "codebot-engine")
        run = lambda *a: subprocess.run([sys.executable, eng, "report", self.name, "--home", self.home, *a], capture_output=True, text=True)
        self.assertIn("# codebot morning report", run().stdout); self.assertTrue(run("--short").stdout.startswith("2/3")); self.assertEqual(json.loads(run("--json").stdout)["counts"]["done"], 2)
        p = run("--write").stdout.strip(); self.assertTrue(os.path.exists(p))

    def test_legacy_project_is_handled(self):
        os.remove(os.path.join(self.state, "tasks.json")); self.assertIsNone(report.build(self.proj()))

if __name__ == "__main__": unittest.main()
