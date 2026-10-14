"""The benchmark tooling, tested without the real model: the suite is solvable and its checks discriminate; preflight catches real misconfiguration;
metrics are computed correctly from trace/attempt/plan records."""
import json, os, shutil, subprocess, sys, unittest
from helpers import Sandboxed
from fake_ollama import FakeOllama, A
from codebot import bench, engine, preflight, worker, checks as C, tasks as T

REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
class BenchHome(Sandboxed):
    """A temp home that has the real suite, modes and config, with the standard bench project laid out."""
    def setUp(self):
        super().setUp()
        for d in ("bench", "modes", "config", "lib", "bin"): shutil.copytree(os.path.join(REPO, d), os.path.join(self.home, d), dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "results", "runs"))
        with open(os.path.join(self.home, "config", "codebot.env"), "a") as f: f.write("\nITER_TIMEOUT=60\nCHECK_TIMEOUT=60\n")
        self.name = bench.setup(self.home, "pyutils", "fixed", "b1"); self.state = os.path.join(self.home, "projects", "b1")
        self.code = os.path.join(self.home, "bench", "runs", "b1", "code")
    def proj(self):
        from codebot.config import Project; return Project("b1", self.home)

class TestSuite(BenchHome):
    def test_every_check_fails_on_the_seed_so_the_guard_accepts_the_plan(self):
        self.set_env_b1 = open(os.path.join(self.state, "project.env"), "a"); self.set_env_b1.write('CHECK_BASELINE_GUARD="1"\n'); self.set_env_b1.close()
        engine.freeze_project(self.proj())                                    # raises if any check already passes or is broken
        d = T.TaskStore(self.proj()).load(); self.assertEqual([t["id"] for t in d["tasks"]], ["T1", "T2", "T3", "T4", "T5"])
        for t in d["tasks"]: self.assertNotEqual(C.run_check(self.proj(), t)["rc"], 0, t["id"]); self.assertTrue(C.run_check(self.proj(), t)["out"].strip(), f"{t['id']} fails silently")

    def test_the_reference_solution_passes_every_check(self):
        engine.freeze_project(self.proj(), allow_passing=True)
        for f in os.listdir(os.path.join(self.home, "bench", "suite", "pyutils", "reference")): shutil.copy(os.path.join(self.home, "bench", "suite", "pyutils", "reference", f), self.code)
        for t in T.TaskStore(self.proj()).load()["tasks"]:
            r = C.run_check(self.proj(), t); self.assertEqual(r["rc"], 0, f"{t['id']}: {r['out']}")

    def test_each_task_is_independent_enough_to_be_judged_alone(self):
        engine.freeze_project(self.proj(), allow_passing=True); ref = os.path.join(self.home, "bench", "suite", "pyutils", "reference")
        for fname, tid in (("textutil.py", "T1"), ("stats.py", "T3"), ("legacy.py", "T5")):          # solving only T1's file must not satisfy other tasks
            for f in os.listdir(self.code):
                if f not in ("stats.py", "legacy.py", "main_app.py", "README.md") and not f.startswith("."): os.remove(os.path.join(self.code, f))
            subprocess.run(["git", "-C", self.code, "checkout", "-q", "--", "."], capture_output=True)
            shutil.copy(os.path.join(ref, fname), self.code)
            if fname == "legacy.py": shutil.copy(os.path.join(ref, "main_app.py"), self.code)
            res = {t["id"]: C.run_check(self.proj(), t)["rc"] == 0 for t in T.TaskStore(self.proj()).load()["tasks"]}
            self.assertTrue(res[tid], (fname, res)); self.assertLessEqual(sum(res.values()), 2, (fname, res))

class TestEngineOnTheSuite(BenchHome):
    def test_scripted_solver_completes_the_suite_and_collect_reports_it(self):
        ref = os.path.join(self.home, "bench", "suite", "pyutils", "reference")
        solver = ("import os, shutil\nref = %r\nt = os.environ['CODEBOT_TASK_ID']\n"
                  "if t == 'T1': open('textutil.py', 'w').write('import re\\n\\n\\ndef slugify(text):\\n    return re.sub(r\"[^a-z0-9]+\", \"-\", text.lower()).strip(\"-\")\\n')\n"
                  "files = {'T2': ['textutil.py'], 'T3': ['stats.py'], 'T4': ['cli.py'], 'T5': ['legacy.py', 'main_app.py']}\n"
                  "for f in files.get(t, []): shutil.copy(os.path.join(ref, f), '.')\n") % ref
        with open(os.path.join(self.state, "project.env"), "a") as f: f.write(f'CHECK_BASELINE_GUARD="1"\nWORKER_CMD="{sys.executable} {self.write_solver(solver)}"\n')
        engine.freeze_project(self.proj())
        for _ in range(8):
            if engine.step(self.proj())["event"] == "all_done": break
        res = bench.collect(self.proj())
        self.assertEqual(res["pass_rate"], {"done": 5, "total": 5, "rate": 1.0}); self.assertEqual(res["attempts_per_task"], 1.0); self.assertEqual(res["guard"]["rejected_total"], 0)
        self.assertEqual(res["ladder"]["escalations"], []); self.assertTrue(all(p["status"] == "done" for p in res["tasks"]))
    def write_solver(self, src):
        p = os.path.join(self.tmp, "solver.py")
        with open(p, "w") as f: f.write(src)
        return p

class TestPreflight(BenchHome):
    def cfg(self, **kv):
        with open(os.path.join(self.home, "config", "codebot.env"), "a") as f:
            for k, v in kv.items(): f.write(f"\n{k}={v}")
    def run_pf(self, fake, **cfg):
        self.fake = fake; self.cfg(OLLAMA_PORT=fake.port, NUM_CTX=32768, KEEP_ALIVE="30m", **cfg)
        class P:
            home = self.home; name = "pf"; mode = "code"; rundir = self.home; state = self.home
            def __init__(s):
                from codebot.config import read_env; s.cfg = read_env(os.path.join(self.home, "config", "codebot.env"))
            def get(s, k, d=None, cast=str):
                v = s.cfg.get(k); 
                try: return cast(v) if v not in (None, "") else d
                except (TypeError, ValueError): return d
            def p(s, *x): return os.path.join(self.home, "logs", *x)
        os.makedirs(os.path.join(self.home, "logs"), exist_ok=True); return preflight.run(P(), self.home)
    def tearDown(self):
        if getattr(self, "fake", None): self.fake.stop()
        super().tearDown()
    def GOOD_PS(self, ctx=32768, mins=29, tz=0):
        import datetime as dt; tzinfo = dt.timezone(dt.timedelta(hours=tz))      # Ollama reports local time WITH an offset; any offset must give the same answer
        return {"models": [{"name": "codebot-model:latest", "context_length": ctx, "expires_at": (dt.datetime.now(tzinfo) + dt.timedelta(minutes=mins)).isoformat()}]}
    def status(self, items, frag): return next(s for n, s, d in items if frag in n)

    def test_all_pass_against_a_well_behaved_server(self):
        fake = FakeOllama(lambda m, b: A("finish", summary="x"), cache="hybrid", ps=self.GOOD_PS()); items = self.run_pf(fake)
        self.assertFalse([i for i in items if i[1] == "FAIL"], items)
        self.assertEqual(self.status(items, "KV cache reused across turns"), "PASS"); self.assertEqual(self.status(items, "grammar"), "PASS")
        self.assertEqual(self.status(items, "(info) same prefix"), "WARN")                      # a hybrid model cannot reuse a prefix that diverges early; reported, not failed
        self.assertIn("NOT re-evaluated", next(d for n, s_, d in items if "KV cache reused" in n))
        r = json.load(open(os.path.join(self.home, "logs", "tokenizer_ratio.json"))); self.assertGreater(r["chars_per_token"], 1.5); self.assertGreater(r["cold_prompt_tps"], 10)
        self.assertIn("PREFLIGHT OK", preflight.render(items))

    def test_fails_when_the_server_ignores_the_schema(self):
        fake = FakeOllama(lambda m, b: "Sure! The ocean is vast and blue.", cache="hybrid", ps=self.GOOD_PS()); items = self.run_pf(fake)
        self.assertEqual(self.status(items, "grammar"), "FAIL"); self.assertIn("PREFLIGHT FAILED", preflight.render(items))

    def test_fails_when_the_cache_is_not_reused(self):
        fake = FakeOllama(lambda m, b: A("finish", summary="x"), cache=False, ps=self.GOOD_PS()); items = self.run_pf(fake)
        self.assertEqual(self.status(items, "KV cache reused across turns"), "FAIL")
    def test_prefix_caching_servers_pass_the_info_check_too(self):
        fake = FakeOllama(lambda m, b: A("finish", summary="x"), cache="prefix", ps=self.GOOD_PS()); items = self.run_pf(fake)
        self.assertEqual(self.status(items, "(info) same prefix"), "PASS")
    def test_a_reply_cut_off_by_the_token_limit_is_not_a_schema_failure(self):
        cut = ('{\n  "why": "The ocean is vast and', "length")
        fake = FakeOllama(lambda m, b: cut if "ocean" in m[-1]["content"] else A("finish", summary="x"), cache="hybrid", ps=self.GOOD_PS()); items = self.run_pf(fake)
        g = next((s_, d) for n, s_, d in items if "grammar" in n); self.assertEqual(g[0], "PASS"); self.assertIn("cut off", g[1])
    def test_prose_that_merely_stops_is_a_schema_failure(self):
        fake = FakeOllama(lambda m, b: ("Lovely ocean prose with no JSON at all", "stop"), cache="hybrid", ps=self.GOOD_PS()); items = self.run_pf(fake)
        self.assertEqual(self.status(items, "grammar"), "FAIL")

    def test_fails_when_the_server_runs_a_different_context(self):
        fake = FakeOllama(lambda m, b: A("finish", summary="x"), cache="hybrid", ps=self.GOOD_PS(ctx=4096)); items = self.run_pf(fake)
        self.assertEqual(self.status(items, "configured context"), "FAIL")

    def test_keep_alive_check_is_timezone_safe(self):
        for tz in (-4, 0, 5):
            fake = FakeOllama(lambda m, b: A("finish", summary="x"), cache="hybrid", ps=self.GOOD_PS(mins=29, tz=tz)); items = self.run_pf(fake)
            self.assertEqual(self.status(items, "stays loaded"), "PASS", tz); fake.stop(); self.fake = None
            fake = FakeOllama(lambda m, b: A("finish", summary="x"), cache="hybrid", ps=self.GOOD_PS(mins=2, tz=tz)); items = self.run_pf(fake)
            self.assertEqual(self.status(items, "stays loaded"), "FAIL", tz); fake.stop(); self.fake = None
    def test_fails_when_the_model_will_unload_soon(self):
        fake = FakeOllama(lambda m, b: A("finish", summary="x"), cache="hybrid", ps=self.GOOD_PS(mins=2)); items = self.run_pf(fake)
        self.assertEqual(self.status(items, "stays loaded"), "FAIL")

    def test_fails_when_configs_disagree(self):
        with open(os.path.join(self.home, "config", "Modelfile"), "w") as f: f.write("FROM x\nPARAMETER num_ctx 8192\n")
        fake = FakeOllama(lambda m, b: A("finish", summary="x"), cache="hybrid", ps=self.GOOD_PS()); items = self.run_pf(fake)
        self.assertEqual(self.status(items, "num_ctx explicit"), "FAIL")

    def test_unreachable_server(self):
        self.cfg(OLLAMA_PORT=1, NUM_CTX=32768, KEEP_ALIVE="30m")
        class P:
            home = self.home; name = "pf"; mode = "code"; rundir = self.home; state = self.home
            cfg = __import__("codebot.config", fromlist=["x"]).read_env(os.path.join(self.home, "config", "codebot.env"))
            def get(s, k, d=None, cast=str):
                v = s.cfg.get(k)
                try: return cast(v) if v not in (None, "") else d
                except (TypeError, ValueError): return d
            def p(s, *x): return os.path.join(self.home, "logs", *x)
        items = preflight.run(P(), self.home); self.assertEqual(self.status(items, "reachable"), "FAIL")

class TestRunStatus(unittest.TestCase):
    C = lambda self, p=0, r=0, d=0, b=0: {"pending": p, "running": r, "done": d, "blocked": b}
    def test_a_loop_that_exits_after_everything_settled_finished_normally(self):
        self.assertEqual(bench.classify(self.C(d=5), 1, False, 600), "finished")          # the bug: this used to be reported as loop_died
    def test_settled_twice_with_the_loop_alive_is_finished(self):
        self.assertEqual(bench.classify(self.C(d=5), 2, True, 600), "finished"); self.assertIsNone(bench.classify(self.C(d=5), 1, True, 600))
    def test_blocked_tasks_are_reported_as_such(self): self.assertEqual(bench.classify(self.C(d=3, b=2), 2, True, 600), "finished_with_blocked")
    def test_a_dead_loop_with_unfinished_work_is_loop_died(self):
        self.assertEqual(bench.classify(self.C(p=2, d=3), 0, False, 600), "loop_died"); self.assertIsNone(bench.classify(self.C(p=2, r=1), 0, False, 30))
    def test_running_work_is_not_a_status(self): self.assertIsNone(bench.classify(self.C(p=2, r=1), 0, True, 600))

class TestTurnStats(unittest.TestCase):
    def turn(self, **kw): return {"event": "turn", "task": "T1", "n": 1, "kind": "ok", "ptok": 1000, "gtok": 50, "prompt_ms": 40000, "gen_ms": 10000, "load_ms": 0, "secs": 50, "est_prompt": 1100, "num_ctx": 32768, **kw}
    # real server semantics: ptok is the TOTAL prompt size; prompt_ms shows what was really evaluated (a cold turn evaluates ~25 tok/s)
    def test_aggregation(self):
        tr = [self.turn(), self.turn(n=2, ptok=1100, prompt_ms=2000, est_prompt=1300), self.turn(n=3, kind="malformed", ptok=1150, prompt_ms=1500, est_prompt=1400), self.turn(n=4, kind="action_error", ptok=1200, prompt_ms=1000, est_prompt=1500), self.turn(n=5, kind="truncated", ptok=1250, prompt_ms=900, est_prompt=1500),
              {"event": "attempt_end", "truncated": 1}, {"event": "schema_fallback"}]
        s = bench.summarize_turns(tr)
        self.assertEqual((s["turns"], s["ok"], s["malformed"], s["action_errors"], s["truncated"]), (5, 2, 1, 1, 1)); self.assertEqual(s["schema_failure_rate"], 0.2)
        self.assertEqual(s["prompt_ms"]["p50"], 1500); self.assertEqual(s["prompt_ms"]["p95"], 40000); self.assertEqual(s["gen_ms"]["mean"], 10000); self.assertEqual(s["gen_tok_per_s"], 5.0)
        self.assertEqual((s["truncated_contexts"], s["schema_fallbacks"], s["max_est_prompt_tokens"]), (1, 1, 1500))
        self.assertEqual(s["cold_prompt_tok_per_s"], 25.0); self.assertEqual(s["cache_reuse_first_turn"], 0.0); self.assertGreater(s["cache_reuse_later_turns"], 0.9)
    def test_no_turns(self): self.assertEqual(bench.summarize_turns([]), {"turns": 0})

class TestCollectFromANativeRun(BenchHome):
    def test_trace_and_ladder_end_up_in_the_report(self):
        with open(os.path.join(self.state, "project.env"), "a") as f: f.write('CHECK_BASELINE_GUARD="1"\nSTUCK_SAME_FAILURE="99"\nSTUCK_NO_PROGRESS="99"\nTASK_MAX_ATTEMPTS="3"\nSANDBOX="none"\n')
        engine.freeze_project(self.proj()); self.fake = FakeOllama([A("list_dir", path="."), A("finish", summary="s")] * 3)
        with open(os.path.join(self.state, "project.env"), "a") as f: f.write(f'WORKER="native"\nOLLAMA_PORT="{self.fake.port}"\n')
        for _ in range(3): engine.step(self.proj())                                                         # T1 never succeeds: ladder + block
        res = bench.collect(self.proj()); self.fake.stop()
        self.assertEqual(res["pass_rate"]["done"], 0); self.assertEqual(res["tasks"][0]["status"], "blocked"); self.assertIn("T1", res["tasks"][0]["blocked_question"])
        self.assertGreater(res["turns"]["turns"], 3); self.assertEqual(res["turns"]["prompt_ms"]["mean"], 2000); self.assertTrue(res["ladder"]["blocks"])
        md = bench.render_md(res, {"status": "finished_with_blocked", "minutes": 1.0, "temps": [70, 80]}, "plan ok")
        for frag in ("Pass rate", "Per turn", "Schema", "Baseline guard", "Ladder steps fired", "Blocked", "Heat"): self.assertIn(frag, md)

if __name__ == "__main__": unittest.main()
