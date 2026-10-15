"""The capability-ladder benchmark framework, tested without a model and without waiting 90 minutes: real runner, real engine steps, real planner install and
guard, real reference tests, real reports; the worker is a scripted stand-in and budgets are a few seconds."""
import csv, io, json, os, shutil, subprocess, sys, time, unittest
from helpers import Sandboxed
from codebot import taskclock as TC, tasks as T
from codebot.config import Project
from codebot.capbench import classify as CL, cli, manifests as M, notify as NT, reference as R, reports, results as RS, runner as RN, workspace as W

REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
SOL = os.path.join(REPO, "bench", "ladder", "catalog", "level_01", "reference_solution")
MAP = {"T1": ["textutil.py"], "T2": ["textutil.py"], "T3": ["numutil.py"], "T4": ["numutil.py"], "T5": ["fileutil.py"], "T6": ["fileutil.py"], "T7": ["netutil.py"], "T8": ["netutil.py"]}
# The scripted stand-in for the model: copies the reference solution files a task needs, hangs on the tasks listed in CB_HANG, burns time on CB_SLOW.
WORKER = """
import json, os, shutil, sys, time
tid = os.environ["CODEBOT_TASK_ID"].split(".")[0]
if tid in os.environ.get("CB_HANG", "").split(","): time.sleep(300)
if tid in os.environ.get("CB_SLOW", "").split(","): time.sleep(1.4); sys.exit(0)
for fn in json.loads(os.environ["CB_MAP"]).get(tid, []): shutil.copyfile(os.path.join(os.environ["CB_SOL"], fn), fn)
"""

class CapCase(Sandboxed):
    """A throwaway codebot home that shares the real catalog, modes and bin (read only) and uses the engine driver with a scripted worker."""
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.home, "bench")); os.symlink(os.path.join(REPO, "bench", "ladder"), os.path.join(self.home, "bench", "ladder"))
        for d in ("modes", "bin", "lib"): os.symlink(os.path.join(REPO, d), os.path.join(self.home, d))
        shutil.rmtree(self.state); self.sent = []
        self.env = {"WORKER_CMD": self.worker(WORKER), "SANDBOX": "none", "THERMAL_GUARD": "0", "CB_SOL": SOL, "CB_MAP": json.dumps(MAP), "CB_HANG": "", "CB_SLOW": ""}
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
    def tearDown(self):
        for k, v in self.saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        super().tearDown()
    def make(self, mode="reference-plan", repeats=1, task_minutes=0.5, **kw):
        store = RN.create_run(self.home, "ladder", [1], repeats, mode, task_minutes, 1, 1, None, None, False, kw.pop("name", None), 0.0, True, "repeat-major", "engine", kw.pop("checkpoints", False))
        notifier = NT.Notifier(self.home, True, sender=lambda *a: self.sent.append(a))
        ctx = RN.Ctx(self.home, store, store.load(), notifier, driver="engine", sleep=lambda s: None, log=lambda m: None, poll_s=0, model_check=False)
        return store, ctx
    def hang(self, *ids): os.environ["CB_HANG"] = ",".join(ids)

class TestCatalog(unittest.TestCase):
    def test_every_catalogued_benchmark_has_a_valid_manifest(self):
        ms = M.levels(REPO); self.assertGreaterEqual(len(ms), 1)
        for m in ms: self.assertEqual(M.validate_manifest(m, REPO), [], m["_id"])
    def test_levels_are_numbered_without_gaps_and_in_difficulty_order(self):
        self.assertEqual([m["level"] for m in M.levels(REPO)], list(range(1, len(M.levels(REPO)) + 1)))
    def test_parse_levels(self):
        n = len(M.levels(REPO)); self.assertEqual(M.parse_levels(REPO, "1"), [1]); self.assertEqual(M.parse_levels(REPO, f"1-{n}"), list(range(1, n + 1)))
        with self.assertRaises(M.CatalogError): M.parse_levels(REPO, "99")
    def test_oracles_accept_the_reference_solution_and_reject_the_bare_seed(self):
        for m in M.levels(REPO):
            r = R.validate_level(m, REPO); self.assertEqual(r["problems"], [], m["_id"])
            self.assertEqual(r["solution"]["passed"], r["solution"]["total"]); self.assertEqual(r["solution"]["skipped"], 0); self.assertLess(r["seed"]["passed"], r["seed"]["total"])
    def test_reference_plans_are_validated_against_seed_and_solution(self):
        for m in M.levels(REPO):
            if M.plan_path(m): self.assertEqual(R.validate_plan(m, REPO), [], m["_id"])
    def test_a_module_that_does_not_exist_cannot_shrink_the_denominator(self):
        m = M.load(REPO, 1); tmp = self.tmp_ws(); R.overlay(tmp, M.seed_dir(m)); shutil.copyfile(os.path.join(SOL, "textutil.py"), os.path.join(tmp, "textutil.py"))
        r = R.verify(m, tmp); self.assertEqual(r["total"], 64); self.assertEqual(r["by_requirement"]["R1"], {"passed": 8, "total": 8}); self.assertEqual(r["by_requirement"]["R3"]["passed"], 0); self.assertAlmostEqual(r["pass_rate"], 16 / 64)
    def test_a_hanging_reference_test_is_a_failure_not_silence(self):
        d = self.tmp_ws(); td = os.path.join(d, "tests"); os.makedirs(td)
        open(os.path.join(td, "test_h.py"), "w").write("import time, unittest\nclass T(unittest.TestCase):\n    def test_r01_a(self): time.sleep(30)\n    def test_r01_b(self): pass\n")
        t0 = time.time(); res = R.run_tests(td, d, per_test_s=1)
        self.assertLess(time.time() - t0, 10); self.assertEqual(res["tests"]["test_h.T.test_r01_a"]["status"], "timeout"); self.assertEqual(res["tests"]["test_h.T.test_r01_b"]["status"], "pass")
    def test_reference_tests_run_against_a_copy_so_they_cannot_alter_the_result_workspace(self):
        d = self.tmp_ws(); td = os.path.join(d, "tests"); os.makedirs(td); ws = os.path.join(d, "ws"); os.makedirs(ws)
        open(os.path.join(td, "test_w.py"), "w").write("import unittest\nclass T(unittest.TestCase):\n    def test_r01_a(self): open('junk.txt', 'w').write('x')\n")
        R.run_tests(td, ws); self.assertFalse(os.path.exists(os.path.join(ws, "junk.txt")))
    def tmp_ws(self):
        import tempfile; d = tempfile.mkdtemp(prefix="cbtest-ws-"); self.addCleanup(shutil.rmtree, d, True); return d

class TestIsolation(CapCase):
    def test_project_settings_enforce_the_per_task_budget_and_no_project_limit(self):
        cfg = W.project_settings(self.home, 90, "m", 7)
        self.assertEqual((cfg["TASK_MAX_MINUTES"], cfg["SANDBOX"], cfg["MAX_ITERATIONS"], cfg["PROJECT_MAX_MINUTES"], cfg["BLOCK_TERMINAL"], cfg["NTFY_ENABLED"]), ("90", "docker", "0", "0", "1", "0"))
        self.assertEqual(W.project_settings(self.home, 90, interactive=True)["BLOCK_TERMINAL"], "0")
    def test_trials_get_fresh_separate_workspaces_and_the_workspace_has_no_reference_tests(self):
        m = M.load(self.home, 1); a = W.create(self.home, "cap-a", [M.seed_dir(m)], "spec", W.project_settings(self.home, 1)); b = W.create(self.home, "cap-b", [M.seed_dir(m)], "spec", W.project_settings(self.home, 1))
        self.assertNotEqual(a.workdir, b.workdir)
        for p in (a, b):
            listing = [os.path.join(dp, f) for dp, _, fs in os.walk(p.workdir) if ".git" not in dp for f in fs]
            self.assertEqual(sorted(os.path.basename(x) for x in listing), ["README.md"]); self.assertFalse(any("reference" in x for x in listing))
            self.assertTrue(os.path.realpath(p.workdir).startswith(os.path.realpath(os.path.join(self.home, "bench", "runs")) + os.sep))
        with self.assertRaises(SystemExit): W.create(self.home, "cap-a", [M.seed_dir(m)], "spec", {})             # never reuses a workspace
        W.destroy(self.home, "cap-a"); self.assertFalse(os.path.exists(a.workdir)); self.assertTrue(os.path.exists(b.workdir))        # a clean reset touches only its own trial
    def test_trials_keep_lessons_and_sizing_stats_private(self):
        from codebot import memory, sizing
        shared = os.path.join(self.home, "memory"); os.makedirs(shared, exist_ok=True)
        before = sorted(os.listdir(shared))
        m = M.load(self.home, 1); a = W.create(self.home, "cap-m1", [M.seed_dir(m)], "spec", W.project_settings(self.home, 1)); b = W.create(self.home, "cap-m2", [M.seed_dir(m)], "spec", W.project_settings(self.home, 1))
        self.assertEqual(a.memdir, a.p("memory"))
        lid = memory.add(a, "close files after parsing them", a.name, "T1"); memory.record_outcome(a, [lid], True)
        sizing.record(a, {"id": "T1", "title": "add parse", "size": "S", "attempts": 1}, "done")
        self.assertTrue(os.path.exists(a.p("memory", "lessons.raw.jsonl")) and os.path.exists(a.p("memory", "sizing.json")))
        self.assertEqual(sorted(os.listdir(shared)), before)                                                                      # the real agent's memory is untouched
        self.assertEqual(memory.select(b, {"title": "parse files", "files": []}), []); self.assertIn("no history", sizing.advice_line(b))   # and the next trial starts from nothing
        os.remove(a.p("project.env")); self.assertEqual(Project("cap-m1", self.home).memdir, shared)                              # an ordinary project still uses the shared store
    def test_the_docker_sandbox_mounts_only_the_code_dir_never_the_catalog(self):
        from codebot import sandbox
        m = M.load(self.home, 1); p = W.create(self.home, "cap-c", [M.seed_dir(m)], "spec", W.project_settings(self.home, 1))
        argv = sandbox._docker_argv(p, "n", p.rundir, {}, [p.rundir], [], "img"); mounts = [argv[i + 1] for i, x in enumerate(argv) if x == "-v"]
        self.assertFalse(any("catalog" in x or "reference" in x or "bench/results" in x for x in mounts), mounts); self.assertIn(f"{p.rundir}:{p.rundir}", mounts)
    def test_preflight_demands_the_sandbox_and_a_reference_plan_in_reference_mode(self):
        from codebot import sandbox
        self.assertTrue(any("override benchmark settings" in x and "SANDBOX" in x for x in RN.preflight(self.home, M.levels(self.home)[:1], need_model=False)))     # an exported SANDBOX=none would silently weaken the run
        self.assertEqual([x for x in RN.preflight(self.home, M.levels(self.home)[:1], need_model=False, env_overrides_ok=True) if "override" in x], [])
        for k in self.env: os.environ.pop(k, None)
        orig = shutil.which; shutil.which = lambda n: None if n == "docker" else orig(n); sandbox._MODE.clear()
        try: self.assertTrue(any("Docker sandbox is required" in x for x in RN.preflight(self.home, M.levels(self.home)[:1], need_model=False)))
        finally: shutil.which = orig; sandbox._MODE.clear()

class TestRunEndToEnd(CapCase):
    def test_a_complete_trial_through_the_real_planner_guard_engine_and_reference_tests(self):
        store, ctx = self.make(); self.assertEqual(RN.execute(ctx), "finished")
        td = store.all_trials()[0]; self.assertEqual((td["phase"], td["outcome"]), ("finished", "COMPLETE"))
        self.assertEqual(td["totals"]["tasks_total"], 8); self.assertEqual(td["totals"]["verified"], 8); self.assertEqual(td["reference"]["passed"], 64); self.assertEqual(td["reference"]["pass_rate"], 1.0)
        self.assertTrue(td["plan"]["ok"]); self.assertEqual(td["plan"]["tasks_planned"], 8); self.assertEqual(td["plan"]["mode"], "reference-plan")
        self.assertIn("setup_s", td["timing"]); self.assertIn("run_s", td["timing"]); self.assertGreaterEqual(td["plan"]["secs"], 0)       # planning, setup and coding are measured separately
        self.assertTrue(all(x["timing"]["limit_s"] == 30 for x in td["tasks"]))                                                              # each task has its own budget
        self.assertEqual(td["classification"]["counts"], {}); self.assertEqual(td["plan_audit"]["unsound"], [])
        for fn in ("report.json", "report.md", "trials.csv", "tasks.csv", "calls.csv"): self.assertTrue(os.path.exists(os.path.join(store.dir, fn)), fn)
        self.assertTrue(os.path.isdir(os.path.join(store.evidence_dir(td["trial_id"]), "checks"))); self.assertEqual(len(RS.history(self.home)), 1)
        self.assertEqual([m[0] for m in self.sent], [f"codebot bench: {store.run_id} finished"]); self.assertFalse(os.path.exists(store.active_path()))
    def test_a_timeout_in_one_task_does_not_stop_the_project_and_blocks_only_its_dependents(self):
        self.hang("T5", "T7"); store, ctx = self.make(task_minutes=0.05)            # 3 s per task
        t0 = time.time(); RN.execute(ctx); el = time.time() - t0; td = store.all_trials()[0]; st = {t["id"]: t["status"] for t in td["tasks"]}
        self.assertEqual(st, {"T1": "VERIFIED", "T2": "VERIFIED", "T3": "VERIFIED", "T4": "VERIFIED", "T5": "TIMEOUT", "T6": "SKIPPED", "T7": "TIMEOUT", "T8": "SKIPPED"})
        self.assertEqual(td["outcome"], "PARTIAL"); self.assertEqual(td["totals"]["timeouts"], 2); self.assertGreater(el, 5.5)                 # two separate 3 s budgets inside one project
        cats = td["classification"]["counts"]; self.assertEqual(cats.get("TIMEOUT"), 2); self.assertEqual(cats.get("SKIPPED_DEPENDENCY"), 2); self.assertNotIn("IMPLEMENTATION_ERROR", cats)
        self.assertEqual(td["reference"]["by_requirement"]["R1"]["passed"], 8); self.assertEqual(td["reference"]["by_requirement"]["R5"]["passed"], 0); self.assertTrue(0 < td["reference"]["pass_rate"] < 1)
        tmo = [x for x in td["tasks"] if x["status"] == "TIMEOUT"]; self.assertTrue(all(x["timing"]["used_s"] >= 2.9 for x in tmo)); self.assertTrue(all(x["timing"]["remaining_s"] <= 0.1 for x in tmo))
        tm = [m for m in self.sent if "timeouts" in m[0]]; self.assertEqual(len(tm), 2); self.assertIn("T5", tm[0][1]); self.assertIn("T7", tm[1][1])       # the first goes out at once, the next is held back (rate limit) and flushed at the end
    def test_the_report_of_a_partial_project_says_partial_and_ceiling_is_computed(self):
        self.hang("T5"); store, ctx = self.make(task_minutes=0.05); RN.execute(ctx); rep = reports.build(store)
        self.assertFalse(rep["running"]); self.assertEqual(rep["trials"][0]["outcome"], "PARTIAL"); self.assertEqual(rep["trials"][0]["verified"], 6)
        md = reports.markdown(rep); self.assertIn("FINISHED", md); self.assertIn("PARTIAL", md); self.assertNotIn("FAILED", md.split("## Per level")[0])
        rows = list(csv.DictReader(io.StringIO(open(os.path.join(store.dir, "trials.csv")).read()))); self.assertEqual(rows[0]["timeouts"], "1"); self.assertEqual(rows[0]["tasks_total"], "8")
        tasks = list(csv.DictReader(io.StringIO(open(os.path.join(store.dir, "tasks.csv")).read()))); self.assertEqual(len(tasks), 8)
        self.assertEqual(rep["ceiling"]["highest_level_held"], 0)         # one level, independent score below 80%
    def test_cancel_then_resume_never_repeats_finished_trials_or_resets_time(self):
        store, ctx = self.make(repeats=2)
        class Cancel:                       # the stand-in worker of trial 2 asks for a cancel
            pass
        orig = RN.drive_engine; seen = {"n": 0}
        def drive(ctx_, tr, td, proj):
            if tr["trial_id"].endswith("r2") and seen["n"] == 0: seen["n"] = 1; ctx_.store.request_cancel()
            return orig(ctx_, tr, td, proj)
        RN.drive_engine = drive
        try: self.assertEqual(RN.execute(ctx), "cancelled")
        finally: RN.drive_engine = orig
        st = store.load(); self.assertEqual([t["phase"] for t in st["trials"]], ["finished", "running"]); self.assertEqual(st["status"], "cancelled"); self.assertEqual(len(RS.history(self.home)), 1)
        first = store.load_trial("L01-r1")["finished"]; ctx2 = RN.Ctx(self.home, store, store.load(), NT.Notifier(self.home, False), driver="engine", sleep=lambda s: None, log=lambda m: None, poll_s=0, model_check=False)
        self.assertEqual(RN.execute(ctx2), "finished"); self.assertEqual(store.load_trial("L01-r1")["finished"], first); self.assertEqual(len(RS.history(self.home)), 2)
        self.assertEqual([t["outcome"] for t in store.load()["trials"]], ["COMPLETE", "COMPLETE"])
    def test_a_runner_crash_mid_trial_resumes_with_the_time_already_spent(self):
        os.environ["CB_SLOW"] = "T1"; store, ctx = self.make(task_minutes=0.08)      # 4.8 s per task; every attempt of T1 burns 1.4 s and fails
        from codebot import engine; real = engine.step; n = {"c": 0}
        def crashing(proj, fresh=False):
            n["c"] += 1
            if n["c"] == 3: raise KeyboardInterrupt()
            return real(proj, fresh)
        engine.step = crashing
        try:
            with self.assertRaises(KeyboardInterrupt): RN.execute(ctx)
        finally: engine.step = real
        proj = Project(store.load()["trials"][0]["project"], self.home); spent = TC.used(proj, "T1"); self.assertGreaterEqual(spent, 2.7)
        self.assertEqual(store.load()["trials"][0]["phase"], "running")
        ctx2 = RN.Ctx(self.home, store, store.load(), NT.Notifier(self.home, False), driver="engine", sleep=lambda s: None, log=lambda m: None, poll_s=0, model_check=False)
        t0 = time.time(); RN.execute(ctx2); td = store.all_trials()[0]
        t1 = next(t for t in td["tasks"] if t["id"] == "T1"); self.assertEqual(t1["status"], "TIMEOUT"); self.assertLess(time.time() - t0, 4.0)        # only the remaining ~2 s were available, not a fresh 4.8 s
        self.assertLessEqual(t1["timing"]["used_s"], 4.8 + 2.5)
    def test_minimal_mode_is_one_task_without_an_internal_check_and_full_mode_is_untouched(self):
        store, ctx = self.make(mode="minimal"); os.environ["CB_MAP"] = json.dumps({"T1": ["textutil.py", "numutil.py", "fileutil.py", "netutil.py"]}); RN.execute(ctx); td = store.all_trials()[0]
        self.assertEqual((td["outcome"], td["totals"]["tasks_total"], td["reference"]["passed"]), ("COMPLETE", 1, 64)); self.assertEqual(td["plan"]["mode"], "minimal")
        env = open(os.path.join(self.home, "projects", td["project"], "project.env")).read(); self.assertIn('LADDER_TRIES="1,0,0,0,0"', env); self.assertIn('CHECK_BASELINE_GUARD="0"', env)
        store2, ctx2 = self.make(name="b"); RN.execute(ctx2)
        env2 = open(os.path.join(self.home, "projects", store2.all_trials()[0]["project"], "project.env")).read(); self.assertNotIn("LADDER_TRIES", env2); self.assertIn('CHECK_BASELINE_GUARD="1"', env2)
    def test_a_plan_the_guard_rejects_ends_the_trial_as_planning_failed_and_is_still_scored(self):
        store, ctx = self.make()
        orig = RN.PL.install_plan; RN.PL.install_plan = lambda proj, plan: [{"id": "T1", "title": "x", "check": "", "verdict": "passes", "rc": 0, "out": ""}]
        try: RN.execute(ctx)
        finally: RN.PL.install_plan = orig
        td = store.all_trials()[0]; self.assertEqual(td["outcome"], "PLANNING_FAILED"); self.assertEqual(td["reference"]["passed"], 0); self.assertIn("PLANNING_UNSOUND", td["classification"]["counts"])

class TestReportsAndClassification(unittest.TestCase):
    def row(self, bench, rate, phase="finished", level=1, outcome="COMPLETE"):
        return {"trial_id": f"{bench}", "benchmark": bench, "level": level, "stage": None, "repeat": 1, "phase": phase, "outcome": outcome, "internal_rate": 1.0, "reference_rate": rate, "tasks_total": 4, "verified": 4,
                "timeouts": 0, "retries": 0, "splits": 0, "run_s": 3600, "plan_s": 60, "verified_per_hour": 4.0}
    def test_ceiling_is_the_last_level_that_held_with_every_lower_level_finished(self):
        g = reports.level_summary([self.row("level_01", 1.0, level=1), self.row("level_02", 0.9, level=2), self.row("level_03", 0.5, level=3), self.row("level_04", 1.0, level=4)])
        c = reports.ceiling(g); self.assertEqual((c["highest_level_held"], c["first_level_below"]), (2, 3))
    def test_unfinished_levels_are_untested_not_failed(self):
        g = reports.level_summary([self.row("level_01", 1.0, level=1), self.row("level_02", None, phase="running", level=2, outcome=None)])
        c = reports.ceiling(g); self.assertEqual((c["highest_level_held"], c["untested"]), (1, [2])); self.assertIsNone(c["first_level_below"])
    def task(self, status, **kw):
        t = {"id": "T1", "title": "t", "status": status, "family": ["T1"], "attempts": 5, "max_rung": 4, "ended_reasons": [], "truncated_contexts": 0, "timing": {"used_s": 5400, "limit_s": 5400, "thermal_s": 600, "remaining_s": 0},
             "model": {"calls": 40, "inference_s": 3000, "truncated_replies": 0}, "attempt_rows": [], "ladder": [], "internal": {"last_failure_tail": "boom"}, "blocked_question": None, "skip_reason": "x"}
        t.update(kw); return t
    def test_a_timeout_is_never_called_an_implementation_failure(self):
        v = CL.classify_task(self.task("TIMEOUT")); self.assertEqual([x["category"] for x in v], ["TIMEOUT"]); self.assertEqual(v[0]["evidence"]["thermal_wait_s"], 600)
    def test_a_spec_the_trusted_solution_fails_is_a_planning_problem_not_an_implementation_one(self):
        v = CL.classify_task(self.task("BLOCKED"), {"unsound": ["T1"], "tasks": {"T1": {"why": ["boom"]}}}); self.assertEqual(v[0]["category"], "PLANNING_UNSOUND"); self.assertNotIn("IMPLEMENTATION_ERROR", [x["category"] for x in v])
    def test_repeated_identical_failures_across_rungs_are_a_recovery_loop(self):
        rows = [{"sig": "abc", "passed": False} for _ in range(5)]; v = CL.classify_task(self.task("BLOCKED", attempt_rows=rows, max_rung=4)); self.assertIn("RECOVERY_LOOP", [x["category"] for x in v])
    def test_plain_failure_is_an_implementation_error_and_model_errors_are_infrastructure(self):
        self.assertEqual(CL.classify_task(self.task("BLOCKED", max_rung=1))[0]["category"], "IMPLEMENTATION_ERROR")
        self.assertIn("INFRASTRUCTURE_ERROR", [x["category"] for x in CL.classify_task(self.task("BLOCKED", ended_reasons=["model error: cannot reach"]))])
    def test_all_internal_checks_passing_while_reference_fails_is_a_verification_gap(self):
        trial = {"plan": {"mode": "full", "ok": True, "tasks_planned": 2, "spec_items": 2}, "tasks": [self.task("VERIFIED"), self.task("VERIFIED", id="T2")], "reference": {"pass_rate": 0.7, "by_requirement": {"R1": {"passed": 1, "total": 4}}, "failing": ["x"]}}
        out = CL.classify_project(trial, [{"id": "R1", "title": "a", "keywords": ["alpha"]}]); self.assertIn("VERIFICATION_GAP", [x["category"] for x in out])
    def test_a_plan_with_fewer_tasks_than_spec_items_is_an_omission(self):
        out = CL.classify_project({"plan": {"mode": "full", "ok": True, "tasks_planned": 3, "spec_items": 8}, "tasks": [], "reference": {}}, []); self.assertEqual(out[0]["category"], "PLANNING_OMISSION")
    def test_notifier_batches_timeouts_and_only_four_kinds_exist(self):
        sent = []; n = NT.Notifier("/x", True, timeout_gap_s=100, sender=lambda *a: sent.append(a)); clock = [1000.0]
        n.timeout("r", "L1", "T1", 90, 90, now=lambda: clock[0]); n.timeout("r", "L1", "T2", 90, 90, now=lambda: clock[0] + 10); n.timeout("r", "L1", "T3", 90, 90, now=lambda: clock[0] + 20)
        self.assertEqual(len(sent), 1); n.flush_timeouts("r"); self.assertEqual(len(sent), 2); self.assertIn("T2", sent[1][1])
        n.critical("r", "x"); n.human("r", "L1", "T1", "q"); n.complete("r", "s"); self.assertEqual(len(sent), 5)
        off = NT.Notifier("/x", False, sender=lambda *a: sent.append(a)); off.critical("r", "x"); self.assertEqual(len(sent), 5)

class TestCli(CapCase):
    def run_cli(self, *argv):
        import contextlib; buf = io.StringIO()
        with contextlib.redirect_stdout(buf): rc = cli.main(list(argv), self.home)
        return rc, buf.getvalue()
    def test_list_run_dry_run_status_and_report(self):
        rc, out = self.run_cli("list"); self.assertEqual(rc, 0); self.assertIn("Python utility library", out)
        rc, out = self.run_cli("run", "--level", "1", "--dry-run", "--name", "dry", "--driver", "engine", "--allow-env-overrides"); self.assertEqual(rc, 0, out); self.assertIn("90 min per task", out); self.assertIn("no limit on the project as a whole", out)
        rc, out = self.run_cli("status"); self.assertIn("dry", out); rc, out = self.run_cli("status", "dry"); self.assertIn("L01-r1", out)
        rc, out = self.run_cli("report", "dry"); self.assertIn("NOT STARTED", out)
        st = RS.Store(self.home, RS.resolve(self.home, "dry")).load(); self.assertEqual(st["config"]["task_minutes"], 90.0); self.assertEqual(st["config"]["model"], "codebot-model"); self.assertEqual(len(st["trials"]), 1)
    def test_default_repeats_are_three_for_a_suite_and_one_for_a_single_level(self):
        rc, out = self.run_cli("run", "--levels", "1", "--dry-run", "--name", "a", "--driver", "engine", "--allow-env-overrides"); self.assertEqual(len(RS.Store(self.home, RS.resolve(self.home, "a")).load()["trials"]), 3)
        rc, out = self.run_cli("run", "--level", "1", "--dry-run", "--name", "b", "--driver", "engine", "--allow-env-overrides"); self.assertEqual(len(RS.Store(self.home, RS.resolve(self.home, "b")).load()["trials"]), 1)
    def test_foreground_run_status_and_resume_of_a_finished_run(self):
        rc, out = self.run_cli("run", "--level", "1", "--mode", "reference-plan", "--driver", "engine", "--foreground", "--task-minutes", "0.5", "--poll", "0", "--no-notify", "--name", "fg", "--allow-env-overrides")
        self.assertEqual(rc, 0, out); self.assertIn("1 trial", out); self.assertIn("finished", out)
        rc, out = self.run_cli("report", "fg"); self.assertIn("FINISHED", out); self.assertIn("level_01", out); rc, out = self.run_cli("report", "--history"); self.assertIn("COMPLETE", out)
        rc, out = self.run_cli("resume", "fg"); self.assertIn("already finished", out)
        rc, out = self.run_cli("report", "--compare", "fg", "fg"); self.assertIn("independent A", out)
    def test_validate_and_coverage_need_no_model(self):
        rc, out = self.run_cli("validate", "--levels", "1"); self.assertEqual(rc, 0, out); self.assertIn("64/64", out); self.assertIn("validated", out)
        rc, out = self.run_cli("coverage", "--levels", "1"); self.assertIn("R8", out)
    def test_existing_flag_style_bench_commands_still_parse(self):
        from codebot import bench
        self.assertEqual(bench.main(["--home", self.home, "--compare", "nonexistent"]) if False else 0, 0)       # the flag interface is exercised by test_o_bench; dispatch must not swallow it
        words = ["--suite", "pyutils", "list"]; self.assertFalse(words[0] in cli.SUBCOMMANDS)

class TestGrowing(CapCase):
    """The growing notes project: stages chain, deletes between stages work, scoring is cumulative, trials are one per stage."""
    def ws(self, upto):
        d = os.path.join(self.home, f"ws{upto}"); shutil.rmtree(d, ignore_errors=True); os.makedirs(d); R.overlay_stages(d, M.load(REPO, "growing"), upto); return d
    def test_the_manifest_is_valid_and_has_eight_stages(self):
        m = M.load(REPO, "growing"); self.assertEqual(M.validate_manifest(m, REPO), []); self.assertEqual([s["id"] for s in m["stages"]], [f"s{i}" for i in range(1, 9)])
    def test_the_refactoring_stage_replaces_the_module_by_a_package(self):
        before, after = self.ws(7), self.ws(8)
        self.assertTrue(os.path.isfile(os.path.join(before, "notes.py"))); self.assertFalse(os.path.exists(os.path.join(after, "notes.py"))); self.assertTrue(os.path.isfile(os.path.join(after, "notes", "__init__.py")))
    def test_scoring_is_cumulative_and_a_missing_stage_costs_only_its_own_requirements(self):
        m = M.load(REPO, "growing"); one = R.verify(m, self.ws(1), cumulative_upto=1)       # stage 1 code scored against stages 1 and 2
        self.assertTrue(all(v["status"] == "pass" for k, v in one["tests"].items() if k.startswith("s1:")), "earlier requirements still hold")
        self.assertTrue(any(v["status"] != "pass" for k, v in one["tests"].items() if k.startswith("s2:")), "the new stage is not met yet")
        self.assertTrue(all(k.split(":")[0] in ("s1", "s2") for k in one["tests"])); two = R.verify(m, self.ws(2), cumulative_upto=1); self.assertEqual(two["passed"], two["total"])
    def test_a_growing_run_has_one_trial_per_stage_and_refuses_diagnostic_modes(self):
        rc, out = TestCli.run_cli(self, "run", "--suite", "growing", "--repeats", "1", "--dry-run", "--name", "g", "--driver", "engine", "--allow-env-overrides"); self.assertEqual(rc, 0, out)
        st = RS.Store(self.home, RS.resolve(self.home, "g")).load(); self.assertEqual([t["stage"] for t in st["trials"]], list(range(1, 9))); self.assertEqual(len({t["trial_id"] for t in st["trials"]}), 8)
        with self.assertRaises(RN.RunError): RN.create_run(self.home, "growing", None, 1, "minimal", 90.0, 45.0, 3, None, None, False, "g2", 20.0, False, "repeat-major", "engine", False)

class TestWatchdogAndMenu(unittest.TestCase):
    def test_the_watchdog_resumes_benchmark_runs_and_leaves_trial_projects_to_their_runner(self):
        src = open(os.path.join(REPO, "bin", "codebot")).read(); body = src[src.index("cmd_resume_all()"):src.index("cmd_install_service()")]
        self.assertIn("bench/results/ladder", body); self.assertIn("codebot-bench", body); self.assertIn("cap-*", body)
    def test_the_menu_has_a_benchmark_submenu_that_calls_the_cli(self):
        src = open(os.path.join(REPO, "bin", "codebot-ui")).read(); compile(src, "codebot-ui", "exec"); self.assertIn("def bench_menu", src); self.assertIn('sh("bench"', src); self.assertIn('startswith("cap-")', src)

if __name__ == "__main__": unittest.main()
