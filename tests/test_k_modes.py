"""F11: a mode is a folder. Adding one needs no code change."""
import json, os, shutil, subprocess, sys, unittest
from helpers import Sandboxed
from fake_ollama import FakeOllama, A
from codebot import modes, engine, packer, sandbox, worker, checks as C, tasks as T

REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
DOCS_PLAN = """
# Goal
Write the user guide.
# Tasks
- [ ] 1. write the install section
  - check: `bash $CODEBOT_CHECKS/has_heading.sh GUIDE.md '# Install'`
  - files: GUIDE.md
"""

class ModeHome(Sandboxed):
    """Temp home that contains a copy of the real modes/ plus a throwaway `docs` mode created with zero code edits."""
    def setUp(self):
        super().setUp(); shutil.copytree(os.path.join(REPO, "modes"), os.path.join(self.home, "modes"))
        d = os.path.join(self.home, "modes", "docs"); os.makedirs(os.path.join(d, "verifiers"))
        def w(rel, text):
            with open(os.path.join(d, rel), "w") as f: f.write(text)
        w("mode.json", json.dumps({"name": "docs", "description": "Write documentation", "extends": "code", "prompts_add": {"plan": "plan.add.md", "work": "work.add.md"},
            "verifier": {"style": "headings", "helpers": ["has_heading.sh"]}, "commands": {"allow": ["cat", "echo", "grep", "ls", "test"], "deny": ["forbidden-word"]},
            "worker_rules": "worker.md", "memory_file": "notes.md", "memory_cap": 400}))
        w("plan.add.md", "DOCS-PLAN-ADDENDUM write prose, one section per task.\n"); w("work.add.md", "DOCS-WORK-ADDENDUM\n")
        w("worker.md", "DOCS-WORKER-RULES: write clear prose.\n"); w("notes.md", "DOCS-NOTES-MARKER use second person.\n")
        w("verifiers/has_heading.sh", '#!/usr/bin/env bash\ngrep -qF -- "$2" "$CODEBOT_RUNDIR/$1" || { echo "missing heading: $2"; exit 1; }\n')

class TestLoader(ModeHome):
    def test_listing_and_inheritance(self):
        names = [d["name"] for d in modes.describe(self.home)]; self.assertEqual(names, ["code", "docs", "re"])
        re_ = modes.load(self.home, "re")
        self.assertEqual((re_["engine"], re_["extends"]), ("native", "code"))                       # engine inherited
        self.assertEqual([os.path.basename(d) for d in re_["chain"]], ["code", "re"])
        self.assertIn("\\brea\\s+(capture", " ".join(re_["commands"]["deny"])); self.assertIn("rea", re_["commands"]["allow"])
        self.assertEqual(re_["prompts"]["plan"], "plan.md")                                           # inherited from code

    def test_prompt_is_base_plus_addendum(self):
        base = modes.prompt_template(self.home, "code", "plan"); docs = modes.prompt_template(self.home, "docs", "plan")
        self.assertTrue(docs.startswith(base.rstrip()[:200])); self.assertIn("DOCS-PLAN-ADDENDUM", docs); self.assertNotIn("DOCS-PLAN", base)
        self.assertIn("REVERSE-ENGINEERING", modes.prompt_template(self.home, "re", "plan"))
        self.assertLess(len(modes.prompt_template(self.home, "code", "plan", quick=True)), len(base))   # quick variant

    def test_errors_are_specific(self):
        with self.assertRaisesRegex(modes.ModeError, r"unknown mode 'nope' \(available: code, docs, re\)"): modes.load(self.home, "nope")
        os.makedirs(os.path.join(self.home, "modes", "bad")); open(os.path.join(self.home, "modes", "bad", "mode.json"), "w").write("{oops")
        with self.assertRaisesRegex(modes.ModeError, "not valid JSON"): modes.load(self.home, "bad")
        self.assertIn("BROKEN", [d for d in modes.describe(self.home) if d["name"] == "bad"][0]["description"])    # one broken mode does not hide the rest
        os.makedirs(os.path.join(self.home, "modes", "loop"))
        open(os.path.join(self.home, "modes", "loop", "mode.json"), "w").write('{"name":"loop","extends":"loop2"}')
        os.makedirs(os.path.join(self.home, "modes", "loop2")); open(os.path.join(self.home, "modes", "loop2", "mode.json"), "w").write('{"name":"loop2","extends":"loop"}')
        with self.assertRaisesRegex(modes.ModeError, "inheritance loop"): modes.load(self.home, "loop")
        os.makedirs(os.path.join(self.home, "modes", "noeng")); open(os.path.join(self.home, "modes", "noeng", "mode.json"), "w").write('{"name":"noeng"}')
        with self.assertRaisesRegex(modes.ModeError, "lacks 'engine'"): modes.load(self.home, "noeng")

class TestNewModeNeedsNoCode(ModeHome):
    def setUp(self):
        super().setUp(); self.set_env(MODE="docs"); self.write_plan(DOCS_PLAN); engine.freeze_project(self.proj())

    def test_verifier_helper_is_frozen_with_the_project(self):
        self.assertTrue(os.path.exists(os.path.join(self.state, "checks", "has_heading.sh")))
        self.assertEqual(C.verify(self.proj(), T.TaskStore(self.proj()).load()), [])
        with open(os.path.join(self.state, "checks", "has_heading.sh"), "a") as f: f.write("# tampered\n")
        self.assertIn("modified:has_heading.sh", C.verify(self.proj(), T.TaskStore(self.proj()).load()))

    def test_check_uses_the_helper(self):
        t = T.TaskStore(self.proj()).load()["tasks"][0]
        r = C.run_check(self.proj(), t); self.assertEqual(r["rc"], 1); self.assertIn("missing heading: # Install", r["out"])
        self.write_code("GUIDE.md", "# Install\nrun it\n"); self.assertEqual(C.run_check(self.proj(), t)["rc"], 0)

    def test_context_and_worker_system_prompt_carry_the_mode(self):
        t = T.TaskStore(self.proj()).load()["tasks"][0]
        text, man = packer.pack(self.proj(), t); self.assertIn("DOCS-NOTES-MARKER", text); self.assertIn("MODE NOTES", [n for n, _ in man["sections"]])
        self.fake = FakeOllama([A("finish", summary="s")]); self.set_env(WORKER="native", OLLAMA_PORT=self.fake.port)
        worker.run_attempt(self.proj(), t, 1, ""); sysmsg = self.fake.requests[0]["messages"][0]["content"]
        self.assertIn("DOCS-WORKER-RULES", sysmsg); self.assertIn("ONE JSON action", sysmsg); self.assertIn("Coding rules", sysmsg)       # mode rules ADDED to the base rules
        self.fake.stop()

    def test_command_allowlist_and_deny_regex(self):
        v = lambda c: modes.command_violation(self.proj(), c)
        for ok in ("cat README.md", "echo hi | grep hi", "FOO=1 ls -la", "test -f GUIDE.md && echo yes", "grep -n x file ; ls"): self.assertIsNone(v(ok), ok)
        for bad, frag in (("python3 -c 'print(1)'", "python3"), ("echo hi && python3 -V", "python3"), ("ls | awk '{print}'", "awk"), ("cat $(ls)", "substitution"),
                          ("echo `id`", "substitution"), ("echo forbidden-word", "forbidden")): self.assertIn(frag, v(bad), bad)
        with self.assertRaises(sandbox.Denied): sandbox.check_command(self.proj(), "python3 x.py")

    def test_unknown_mode_in_project_env_fails_clearly(self):
        self.set_env(MODE="nope")
        with self.assertRaises(modes.ModeError): modes.load(self.home, self.proj().mode)

class TestReMode(ModeHome):
    def test_rea_policy(self):
        self.set_env(MODE="re"); p = self.proj(); v = lambda c: modes.command_violation(p, c)
        for ok in ("rea search /t 'x' --mode regex --filter-output normalized_result", "rea decompile /t main | head -40", "grep -c foo findings.md", "cat findings.md"): self.assertIsNone(v(ok), ok)
        for bad in ("rea capture-process x", "rea observe-native-calls /t {}", "rea setup", "rea update", "rea uninstall", "adb devices", "gdb ./x", "strace ls", "/t/target", "./target"):
            self.assertIsNotNone(v(bad), bad)

    def test_sandbox_config_for_re(self):
        self.set_env(MODE="re"); cfg = modes.sandbox_cfg(self.proj())
        self.assertEqual(cfg["memory"], "4g"); self.assertIn("JAVA_HOME", cfg["env"]); self.assertTrue(all(os.path.exists(p) for p in cfg["ro"]))
        if shutil.which("node"): self.assertTrue(any(p.endswith("/bin") and "node" in p for p in cfg["path_prepend"]))

    def test_mounts_and_env_reach_the_docker_command_line(self):
        self.set_env(MODE="re", SANDBOX_RO="/etc/hostname"); p = self.proj(); cfg = modes.sandbox_cfg(p)
        argv = sandbox._docker_argv(p, "n", p.rundir, {"PATH": "/usr/bin"}, [p.rundir], [], "ubuntu:24.04"); line = " ".join(argv)
        self.assertIn("--network none", line); self.assertIn("-v /etc/hostname:/etc/hostname:ro", line); self.assertIn("--memory 4g", line)
        for ro in cfg["ro"]: self.assertIn(f"-v {ro}:{ro}:ro", line)

    def test_code_mode_has_no_command_restrictions_and_no_extra_mounts(self):
        p = self.proj(); self.assertIsNone(modes.command_violation(p, "python3 -c 'print(1)' && ls $(pwd)")); self.assertEqual(modes.sandbox_cfg(p)["ro"], [])

class TestBash(ModeHome):
    def setUp(self):
        super().setUp()
        for d in ("bin", "lib", "memory", "config"): shutil.copytree(os.path.join(REPO, d), os.path.join(self.home, d), dirs_exist_ok=True, ignore=shutil.ignore_patterns("local.env", "__pycache__", "projects", "*.snap"))
        self.set_env(MODE="docs")
    def cli(self, *a): return subprocess.run([os.path.join(self.home, "bin", "codebot"), *a], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    def test_modes_command_lists_the_new_mode(self):
        out = self.cli("modes").stdout; self.assertIn("docs", out); self.assertIn("Write documentation", out); self.assertIn("extends code", out)
    def test_prompt_uses_the_mode(self):
        out = self.cli("prompt", self.name, "plan").stdout; self.assertIn("DOCS-PLAN-ADDENDUM", out); self.assertNotIn("{{", out)
        self.assertIn("DOCS-WORK-ADDENDUM", self.cli("prompt", self.name, "work").stdout)
    def test_unknown_mode_gives_a_helpful_error(self):
        self.set_env(MODE="nope"); r = self.cli("status", self.name); self.assertNotEqual(r.returncode, 0); self.assertIn("unknown mode 'nope'", r.stderr)
    def test_plan_flags_are_stored_in_project_env(self):
        self.cli("plan", "p2", self.code, "--mode", "re", "--target", "/etc/hostname", "--quick")      # the interactive part cannot start here; the setup before it can
        env = open(os.path.join(self.home, "projects", "p2", "project.env")).read(); self.assertIn('MODE="re"', env); self.assertIn('SANDBOX_RO="/etc/hostname"', env)

if __name__ == "__main__": unittest.main()
