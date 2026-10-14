"""F2: a verified task is a commit + tag; retry-fresh resets to the last good state; baseline protects user files."""
import json, os, subprocess, unittest
from helpers import Sandboxed, PLAN2
from codebot import engine, gitops as G

OK1 = "import os\nopen('a.txt','w').write('hi\\n')\n"
OK2 = "import os\nopen('b.txt','w').write('yo\\n')\n"
JUNK = "open('junk.txt','w').write('x')\nopen('README.md','a').write('vandalised\\n')\n"

class Base(Sandboxed):
    def setUp(self):
        super().setUp(); self.write_plan(PLAN2); engine.freeze_project(self.proj())
    def log(self): return subprocess.run(["git", "-C", self.code, "log", "--format=%s"], capture_output=True, text=True).stdout.splitlines()
    def tags(self): return subprocess.run(["git", "-C", self.code, "tag"], capture_output=True, text=True).stdout.split()
    def exists(self, rel): return os.path.exists(os.path.join(self.code, rel))
    def run_worker(self, src): self.set_env(WORKER_CMD=self.worker(src))

class TestCheckpoints(Base):
    def test_verified_task_is_a_commit_and_a_tag(self):
        self.run_worker(OK1); n0 = len(self.log())
        e = engine.step(self.proj()); self.assertEqual(e["event"], "task_done")
        self.assertTrue(self.log()[0].startswith("codebot: T1 Create a.txt"))
        self.assertIn("codebot/t1/T1", self.tags())
        cp = G.load(self.proj()); self.assertEqual(cp["list"][0]["task"], "T1"); self.assertEqual(cp["list"][0]["sha"], G.head(self.code))
        self.assertEqual(len(self.log()), n0 + 1)  # clean tree: baseline is the existing HEAD, so only T1 is new

    def test_failed_attempt_makes_no_commit(self):
        self.run_worker(JUNK); engine.step(self.proj()); engine.step(self.proj())
        self.assertFalse(any(s.startswith("codebot: T") for s in self.log())); self.assertEqual(self.tags(), [])

    def test_dirty_tree_gets_a_baseline_commit(self):
        self.write_code("wip.py", "unfinished\n"); n0 = len(self.log())
        self.run_worker(OK1); engine.step(self.proj())
        self.assertEqual(len(self.log()), n0 + 2)
        self.assertTrue(any("baseline" in s for s in self.log()))

    def test_baseline_captures_preexisting_untracked_files(self):
        self.write_code("mine.py", "precious\n")           # untracked user file before the run
        self.run_worker(JUNK); engine.step(self.proj(), fresh=True)
        self.assertTrue(self.exists("mine.py")); self.assertEqual(G.git(self.code, "status", "--porcelain").stdout.strip().splitlines().count("?? mine.py"), 0)

class TestResetFresh(Base):
    def test_fresh_retry_restores_last_good(self):
        self.run_worker(JUNK); engine.step(self.proj()); engine.step(self.proj())
        self.assertTrue(self.exists("junk.txt"))                        # junk accumulated across plain retries
        self.run_worker("import os\nassert not os.path.exists('junk.txt'), 'junk survived reset'\nassert 'vandalised' not in open('README.md').read()\nopen('a.txt','w').write('hi\\n')\n")
        e = engine.step(self.proj(), fresh=True)
        self.assertEqual(e["event"], "task_done", e)                    # worker itself asserted a clean tree
        self.assertFalse(self.exists("junk.txt"))

    def test_reset_goes_to_last_verified_task_not_baseline(self):
        self.run_worker(OK1); engine.step(self.proj())                  # T1 verified
        self.run_worker(JUNK); engine.step(self.proj())                 # T2 fails, leaves junk
        engine.step(self.proj(), fresh=True)                            # reset, junk worker runs again
        self.assertTrue(self.exists("a.txt"))                           # T1's work survived the reset
        engine.reset(self.proj())
        self.assertTrue(self.exists("a.txt")); self.assertFalse(self.exists("junk.txt"))
        self.assertEqual(G.head(self.code), G.load(self.proj())["list"][-1]["sha"])

    def test_state_dir_untouched_by_reset(self):
        self.run_worker(JUNK); engine.step(self.proj(), fresh=True)
        for f in ("tasks.json", "checks.lock", "checks/T1.sh", "attempts.jsonl"):
            self.assertTrue(os.path.exists(os.path.join(self.state, f)), f)

    def test_ignored_files_survive_reset(self):
        self.write_code(".gitignore", "venv/\n"); self.write_code("venv/keep.txt", "k\n")
        self.run_worker(JUNK); engine.step(self.proj()); engine.step(self.proj(), fresh=True)
        self.assertTrue(self.exists("venv/keep.txt"))

    def test_non_repo_dir_is_initialised(self):
        import shutil; shutil.rmtree(os.path.join(self.code, ".git"))
        self.run_worker(OK1); e = engine.step(self.proj()); self.assertEqual(e["event"], "task_done")
        self.assertTrue(G.is_repo(self.code))

if __name__ == "__main__": unittest.main()
