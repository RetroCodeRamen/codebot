"""F10: commands and checks run offline in a Docker sandbox; everything is written to a tamper-evident audit log."""
import json, os, shutil, subprocess, time, unittest
from helpers import Sandboxed
from fake_ollama import FakeOllama, A
from codebot import audit, sandbox, engine, worker, checks as C, tasks as T

def docker_ready():
    if not shutil.which("docker"): return False
    return subprocess.run(["docker", "image", "inspect", "ubuntu:24.04"], capture_output=True).returncode == 0

PLAN = """
# Tasks
- [ ] 1. make a.txt say hi
  - check: `cat a.txt 2>&1; grep -qx hi a.txt`
"""
class Base(Sandboxed):
    def setUp(self): super().setUp(); self.write_plan(PLAN); engine.freeze_project(self.proj()); self.fake = None
    def tearDown(self):
        if self.fake: self.fake.stop()
        sandbox._MODE.clear(); super().tearDown()
    def p(self): return self.proj()

class TestAuditChain(Base):
    def test_chain_verifies_and_detects_edits(self):
        for i in range(5): audit.log(self.p(), "command", command=f"echo {i}", rc=0, sandbox="docker:net=none:ro-root")
        self.assertEqual(audit.verify(self.p()), (True, 5, ""))
        path = os.path.join(self.state, "audit.jsonl"); lines = open(path).read().splitlines()
        e = json.loads(lines[2]); e["command"] = "echo nothing to see"; lines[2] = json.dumps(e)
        open(path, "w").write("\n".join(lines) + "\n")
        ok, n, why = audit.verify(self.p()); self.assertFalse(ok); self.assertIn("hash mismatch at entry 3", why)

    def test_deleting_a_middle_entry_is_detected(self):
        for i in range(5): audit.log(self.p(), "command", command=str(i), rc=0)
        path = os.path.join(self.state, "audit.jsonl"); lines = open(path).read().splitlines(); del lines[1]
        open(path, "w").write("\n".join(lines) + "\n"); self.assertFalse(audit.verify(self.p())[0])

    def test_cutting_the_tail_is_detected_while_head_is_intact(self):
        for i in range(5): audit.log(self.p(), "command", command=str(i), rc=0)
        path = os.path.join(self.state, "audit.jsonl"); lines = open(path).read().splitlines()
        open(path, "w").write("\n".join(lines[:3]) + "\n"); ok, n, why = audit.verify(self.p()); self.assertFalse(ok); self.assertIn("tail was cut", why)

    def test_empty_log_is_ok(self): self.assertEqual(audit.verify(self.p()), (True, 0, ""))

    def test_concurrent_writers_keep_the_chain_valid(self):
        import threading
        ts = [threading.Thread(target=lambda: [audit.log(self.p(), "command", command="x", rc=0) for _ in range(15)]) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(audit.verify(self.p()), (True, 60, ""))

class TestPolicyAndHooks(Base):
    def test_denied_commands_are_audited(self):
        with self.assertRaises(sandbox.Denied): sandbox.run(self.p(), "curl http://example.com", task="T1")
        e = [x for x in audit.entries(self.p()) if x["kind"] == "command_denied"]; self.assertEqual(len(e), 1); self.assertIn("offline", e[0]["reason"])

    def test_unsandboxed_run_is_flagged_by_the_report(self):
        self.set_env(SANDBOX="none"); sandbox.run(self.p(), "echo hi", task="T1")
        r = audit.report(self.p()); self.assertEqual((r["commands"], r["unsandboxed"]), (1, 1)); self.assertTrue(any("NOT sandboxed" in w for w in r["warnings"]))

    def test_worker_actions_are_audited(self):
        self.fake = FakeOllama([A("write_file", path="a.txt", content="hi\n"), A("edit_file", path="a.txt", search="hi", replace="ho"), A("read_file", path="a.txt"),
                                A("run", command="cat a.txt"), A("finish", summary="s")])
        self.set_env(WORKER="native", OLLAMA_PORT=self.fake.port, SANDBOX="none")
        p = self.p(); task = T.TaskStore(p).load()["tasks"][0]; worker.run_attempt(p, task, 1, "")
        kinds = [e["kind"] for e in audit.entries(p)]
        self.assertEqual(kinds.count("model_request"), 5); self.assertEqual(kinds.count("file_write"), 2); self.assertIn("file_read", kinds); self.assertIn("command", kinds)
        w = [e for e in audit.entries(p) if e["kind"] == "file_write"]; self.assertIsNone(w[0]["sha_before"]); self.assertEqual(w[1]["sha_before"], w[0]["sha_after"])
        self.assertTrue(all(e["endpoint"] == f"127.0.0.1:{self.fake.port}" for e in audit.entries(p) if e["kind"] == "model_request"))
        r = audit.report(p); self.assertEqual(r["model_endpoints"], [f"127.0.0.1:{self.fake.port}"]); self.assertTrue(r["chain_ok"])

    def test_check_runs_are_audited(self):
        self.set_env(SANDBOX="none"); C.run_check(self.p(), T.TaskStore(self.p()).load()["tasks"][0])
        self.assertEqual([e["kind"] for e in audit.entries(self.p())], ["check_run"])

    def test_render_mentions_every_section(self):
        self.set_env(SANDBOX="none"); sandbox.run(self.p(), "echo hi"); out = audit.render(audit.report(self.p()))
        for s in ("chain:", "sandbox:", "commands:", "files:", "model:", "egress:", "verdict:"): self.assertIn(s, out)

@unittest.skipUnless(docker_ready(), "docker + a local ubuntu:24.04 image are needed")
class TestDockerSandbox(Base):
    def setUp(self): super().setUp(); self.set_env(SANDBOX="docker")
    def run_cmd(self, c, **kw): return sandbox.run(self.p(), c, timeout=kw.get("timeout", 60))

    def test_basic_tools_and_file_ownership(self):
        rc, out = self.run_cmd("python3 --version && git --version && echo data > made.txt"); self.assertEqual(rc, 0, out)
        self.assertEqual(os.stat(os.path.join(self.code, "made.txt")).st_uid, os.getuid())

    def test_no_network_at_all(self):
        rc, out = self.run_cmd("python3 -c \"import socket; socket.create_connection(('1.1.1.1',53),3)\""); self.assertNotEqual(rc, 0)
        rc, out = self.run_cmd("getent hosts example.com"); self.assertNotEqual(rc, 0)
        self.assertEqual(sandbox.probe(self.p())["blocked"], True)

    def test_home_and_state_are_invisible(self):
        # bypass the policy layer on purpose: this tests the container boundary itself
        rc, out, _ = sandbox.execute(self.p(), ["bash", "-c", f"ls {os.path.expanduser('~')} 2>&1; ls {self.state} 2>&1; cat {self.state}/tasks.json 2>&1"], timeout=30)
        self.assertEqual(out.count("No such file"), 3, out)

    def test_policy_refuses_state_paths_before_docker_is_even_used(self):
        with self.assertRaises(sandbox.Denied): self.run_cmd(f"cat {self.state}/tasks.json")

    def test_cannot_write_outside_the_project(self):
        rc, out = self.run_cmd("touch /usr/x 2>&1; touch /etc/x 2>&1; touch ../escape.txt 2>&1; touch /bin/y 2>&1"); self.assertGreaterEqual(out.count("denied") + out.count("Read-only"), 4)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.code), "escape.txt")))

    def test_no_capabilities_and_no_new_privs(self):
        rc, out = self.run_cmd("grep -E 'CapEff|NoNewPrivs' /proc/self/status"); self.assertIn("CapEff:\t0000000000000000", out); self.assertIn("NoNewPrivs:\t1", out)

    def test_timeout_kills_the_container(self):
        t0 = time.time(); rc, out = self.run_cmd("sleep 30", timeout=3); self.assertEqual(rc, 124); self.assertLess(time.time() - t0, 15)
        time.sleep(1); ps = subprocess.run(["docker", "ps", "--filter", "name=cb-", "-q"], capture_output=True, text=True).stdout.strip(); self.assertEqual(ps, "")

    def test_memory_limit_applies(self):
        self.set_env(SANDBOX_MEMORY="64m"); sandbox._MODE.clear()
        rc, out = self.run_cmd("python3 -c \"x = bytearray(400*1024*1024); print('allocated')\"", timeout=60); self.assertNotIn("allocated", out)

    def test_checks_run_in_the_sandbox_with_the_locked_script_read_only(self):
        d = T.TaskStore(self.p()).load(); t = d["tasks"][0]; self.write_code("a.txt", "hi\n")
        res = C.run_check(self.p(), t); self.assertEqual(res["rc"], 0)
        e = [x for x in audit.entries(self.p()) if x["kind"] == "check_run"][0]; self.assertTrue(e["sandbox"].startswith("docker"))
        script = os.path.join(self.state, "checks", "T1.sh")
        rc, out, _ = sandbox.execute(self.p(), ["bash", "-c", f"echo hacked >> {script}"], timeout=30, ro=[os.path.join(self.state, "checks")])
        self.assertNotEqual(rc, 0); self.assertNotIn("hacked", open(script).read())

    def test_end_to_end_engine_step_is_offline_and_audited(self):
        self.fake = FakeOllama([A("run", command="python3 -c \"open('a.txt','w').write('hi\\\\n')\""), A("finish", summary="s")])
        self.set_env(WORKER="native", OLLAMA_PORT=self.fake.port)
        e = engine.step(self.p()); self.assertEqual(e["event"], "task_done", e)
        r = audit.report(self.p(), sandbox.probe(self.p()))
        self.assertTrue(r["chain_ok"]); self.assertEqual(r["unsandboxed"], 0); self.assertEqual(r["probe"]["blocked"], True); self.assertEqual(r["warnings"], [])
        self.assertIn("OFFLINE-CLEAN", audit.render(r))

class TestAuditCli(Base):
    def test_cli_exit_codes(self):
        self.set_env(SANDBOX="none"); sandbox.run(self.p(), "echo hi")
        r = subprocess.run(["python3", os.path.join(os.path.dirname(__file__), "..", "bin", "codebot-engine"), "audit", self.name, "--home", self.home], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1); self.assertIn("NOT sandboxed", r.stdout)       # unsandboxed command = warning = nonzero
        with open(os.path.join(self.state, "audit.jsonl"), "a") as f: f.write('{"forged": true}\n')
        r = subprocess.run(["python3", os.path.join(os.path.dirname(__file__), "..", "bin", "codebot-engine"), "audit", self.name, "--home", self.home, "--json"], capture_output=True, text=True)
        self.assertFalse(json.loads(r.stdout)["chain_ok"])

if __name__ == "__main__": unittest.main()
