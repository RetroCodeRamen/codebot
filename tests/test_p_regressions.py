"""Regressions found by the first real-model benchmark run (none of the earlier tests used a worktree code dir or ran the check from the worker)."""
import json, os, shutil, subprocess, sys, unittest
from helpers import Sandboxed
from fake_ollama import FakeOllama, A
from codebot import engine, worker, sandbox, tasks as T

REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
def docker_ready(): return bool(shutil.which("docker")) and subprocess.run(["docker", "image", "inspect", "ubuntu:24.04"], capture_output=True).returncode == 0
PLAN = "# Tasks\n- [ ] 1. make a.txt say hi\n  - check: `python3 $CODEBOT_CHECKS/t.py`\n"
CHECK = "import sys\nsys.exit(0 if open('a.txt').read().strip() == 'hi' else 1)\n"

class WorktreeHome(Sandboxed):
    def setUp(self):
        super().setUp()
        for d in ("bin", "lib", "modes", "memory", "config"): shutil.copytree(os.path.join(REPO, d), os.path.join(self.home, d), dirs_exist_ok=True, ignore=shutil.ignore_patterns("local.env", "__pycache__", "projects", "*.snap"))
        self.fake = FakeOllama(); open(os.path.join(self.home, "config", "codebot.env"), "a").write(f"\nOLLAMA_PORT={self.fake.port}\nNTFY_ENABLED=0\nPHONE_CONTROL=0\nITER_TIMEOUT=30\n")
        os.makedirs(os.path.join(self.state, "checks")); open(os.path.join(self.state, "checks", "t.py"), "w").write(CHECK); self.write_plan(PLAN)
        self.set_env(WORKER_CMD=self.worker("pass"), CHECK_BASELINE_GUARD="1")
        open(os.path.join(self.state, "PAUSE"), "w").close()                      # the loop starts paused: we only test the setup
    def tearDown(self): self.cli("stop", self.name); self.fake.stop(); super().tearDown()
    def cli(self, *a):
        env = dict(os.environ); env.pop("INVOCATION_ID", None)
        return subprocess.run([os.path.join(self.home, "bin", "codebot"), *a], capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env)
    def env(self): return open(os.path.join(self.state, "project.env")).read()

class TestWorktreeLocation(WorktreeHome):
    def test_new_worktree_is_outside_the_state_dir_but_inside_the_codebot_dir(self):
        r = self.cli("start", self.name, "--keep"); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rundir = [l.split("=", 1)[1].strip('"') for l in self.env().splitlines() if l.startswith("RUNDIR=")][0]
        self.assertEqual(rundir, os.path.join(self.home, "workspaces", self.name))
        self.assertFalse(os.path.realpath(rundir).startswith(os.path.realpath(self.state) + os.sep)); self.assertTrue(os.path.isdir(os.path.join(rundir, ".git")) or os.path.isfile(os.path.join(rundir, ".git")))
        self.assertIn("workspaces", subprocess.run(["git", "-C", self.code, "worktree", "list"], capture_output=True, text=True).stdout)

    def test_worker_can_actually_write_in_the_new_worktree(self):
        self.cli("start", self.name, "--keep"); self.cli("stop", self.name)
        p = self.proj(); self.assertNotIn(os.path.realpath(p.state), os.path.realpath(p.rundir))
        self.fake.script = [A("write_file", path="a.txt", content="hi\n"), A("run", command="python3 $CODEBOT_CHECKS/t.py; echo rc=$?"), A("finish", summary="s")]
        self.set_env(WORKER="native", WORKER_CMD="", SANDBOX="none"); task = T.TaskStore(p).load()["tasks"][0]
        os.environ.pop("WORKER_CMD", None); p = self.proj(); worker.run_attempt(p, task, 1, "")
        self.assertEqual(open(os.path.join(p.rundir, "a.txt")).read(), "hi\n")

    def test_an_old_worktree_inside_the_state_dir_is_migrated(self):
        old = os.path.join(self.state, "work")
        subprocess.run(["git", "-C", self.code, "worktree", "add", "-q", "-b", f"codebot/{self.name}", old], check=True)
        self.set_env(RUNDIR=old); open(os.path.join(self.state, ".git-prepped"), "w").close()
        r = self.cli("start", self.name, "--keep"); self.assertIn("migrated the worktree", r.stdout, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(old)); self.assertTrue(os.path.isdir(os.path.join(self.home, "workspaces", self.name)))
        self.assertIn(f'RUNDIR="{os.path.join(self.home, "workspaces", self.name)}"', self.env())

class TestEngineGuard(Sandboxed):
    def test_a_code_dir_inside_the_state_dir_is_refused_loudly(self):
        self.write_plan("# Tasks\n- [ ] 1. t\n  - check: `false`\n"); engine.freeze_project(self.proj())
        inner = os.path.join(self.state, "work"); os.makedirs(inner); self.set_env(RUNDIR=inner)
        e = engine.step(self.proj()); self.assertEqual(e["event"], "error"); self.assertIn("inside the harness state directory", e["error"]); self.assertIn("codebot start", e["error"])

class TestRunHasTheCheck(Sandboxed):
    def setUp(self):
        super().setUp(); os.makedirs(os.path.join(self.state, "checks")); open(os.path.join(self.state, "checks", "t.py"), "w").write(CHECK)
        self.write_plan(PLAN); engine.freeze_project(self.proj(), allow_passing=True); self.fake = None
    def tearDown(self):
        if self.fake: self.fake.stop()
        sandbox._MODE.clear(); super().tearDown()
    def test_run_can_execute_the_frozen_check_by_its_env_var(self):
        self.set_env(SANDBOX="none"); sandbox._MODE.clear(); p = self.proj()
        rc, out = sandbox.run(p, "python3 $CODEBOT_CHECKS/t.py; echo rc=$?; echo $CODEBOT_RUNDIR"); self.assertIn("rc=1", out); self.assertIn(p.rundir, out)
        open(os.path.join(self.code, "a.txt"), "w").write("hi\n"); self.assertIn("rc=0", sandbox.run(p, "python3 $CODEBOT_CHECKS/t.py; echo rc=$?")[1])
    @unittest.skipUnless(docker_ready(), "needs docker")
    def test_inside_docker_the_check_is_runnable_but_read_only(self):
        self.set_env(SANDBOX="docker"); sandbox._MODE.clear(); p = self.proj(); open(os.path.join(self.code, "a.txt"), "w").write("hi\n")
        self.assertIn("rc=0", sandbox.run(p, "python3 $CODEBOT_CHECKS/t.py; echo rc=$?")[1])
        rc, out = sandbox.run(p, "echo 'import sys; sys.exit(0)' > $CODEBOT_CHECKS/t.py 2>&1; echo rc=$?"); self.assertNotIn("rc=0", out)
        self.assertEqual(open(os.path.join(self.state, "checks", "t.py")).read(), CHECK)

OPENCODE = os.path.join(REPO, "tools", "node_modules", ".bin", "opencode")
@unittest.skipUnless(os.path.exists(OPENCODE), "opencode is not installed in this checkout")
class TestOpencodeAgentCanWriteFiles(unittest.TestCase):
    """`"patch": false` in an opencode agent config is mapped onto the whole edit permission and silently removes BOTH write and edit.
    Everything built on opencode (the interactive planner, the legacy loop, the RE mode) could then only write files through bash heredocs."""
    def agent(self, cfg, name):
        r = subprocess.run([OPENCODE, "debug", "agent", name], capture_output=True, text=True, timeout=90, env=dict(os.environ, OPENCODE_CONFIG=os.path.join(REPO, "config", cfg)))
        return json.loads(r.stdout[r.stdout.index("{"):])
    def test_the_coding_agent_has_write_and_edit(self):
        a = self.agent("opencode.json", "codebot"); self.assertTrue(a["tools"]["write"]); self.assertTrue(a["tools"]["edit"])
        self.assertEqual([p for p in a["permission"] if p["permission"] == "edit" and p["action"] == "deny"], [])
    def test_the_re_agent_has_write_and_edit(self):
        subprocess.run([sys.executable, os.path.join(REPO, "bin", "codebot-mkconfig")], capture_output=True)
        a = self.agent("opencode-re.json", "codebot-re"); self.assertTrue(a["tools"]["write"]); self.assertTrue(a["tools"]["edit"])
    def test_the_config_never_disables_patch_again(self):
        for f in ("opencode.json", "opencode-re.json"):
            with open(os.path.join(REPO, "config", f)) as fh: c = json.load(fh)
            for ag in c["agent"].values(): self.assertNotIn("patch", {k for k, v in ag["tools"].items() if v is False})

if __name__ == "__main__": unittest.main()
