"""Supervisor integration: the real bash loop + engine + scripted worker + a fake ntfy server that records pushes."""
import http.server, json, os, shutil, subprocess, sys, threading, time, unittest
from helpers import Sandboxed
from codebot import tasks as T

REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

class FakeNtfy:
    def __init__(self):
        self.msgs = []; self.gets = []; outer = self
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0)); body = self.rfile.read(n).decode()
                outer.msgs.append({"path": self.path, "title": self.headers.get("Title", ""), "prio": self.headers.get("Priority", ""), "body": body})
                self.send_response(200); self.end_headers()
            def do_GET(self): outer.gets.append(self.path); self.send_response(200); self.end_headers()
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
    def titles(self): return [m["title"] for m in self.msgs]
    def stop(self): self.srv.shutdown(); self.srv.server_close()

PLAN = """
# Goal
x
# Tasks
- [ ] 1. make a.txt say hi
  - check: `cat a.txt 2>&1; grep -qx hi a.txt`
- [ ] 2. make b.txt say yo
  - check: `cat b.txt 2>&1; grep -qx yo b.txt`
"""
WORKER = """
import os
t = os.environ['CODEBOT_TASK_ID']; st = os.environ['CODEBOT_STATE']
if t == 'T1': open('a.txt', 'w').write('hi\\n')
if t == 'T2' and os.path.exists(os.path.join(st, 'fixable')): open('b.txt', 'w').write('yo\\n')
"""

class LoopBase(Sandboxed):
    def setUp(self):
        super().setUp()
        for d in ("bin", "lib", "modes", "memory", "config"):
            shutil.copytree(os.path.join(REPO, d), os.path.join(self.home, d), dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "projects", "*.snap"))
        self.ntfy = FakeNtfy()
        with open(os.path.join(self.home, "config", "codebot.env"), "w") as f:
            f.write(f'NTFY_SERVER="http://127.0.0.1:{self.ntfy.port}"\nNTFY_TOPIC="t"\nITER_TIMEOUT=60\nCHECK_TIMEOUT=30\nOLLAMA_PORT=1\n'
                    'CODEBOT_MODEL="x"\nSTALL_LIMIT=3\nMAX_ITERATIONS=300\nLESSONS_MAX=4000\nTHINK=off\n')
        self.write_plan(PLAN); self.set_env(WORKER_CMD=self.worker(WORKER), IDLE_POLL="1", TASK_MAX_ATTEMPTS="3", STUCK_SAME_FAILURE="99",
                                            STUCK_NO_PROGRESS="99", PINGPONG_WINDOW="0")
        r = subprocess.run([sys.executable, os.path.join(self.home, "bin", "codebot-engine"), "freeze", self.name, "--home", self.home], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.loop = None
    def start_loop(self):
        env = dict(os.environ, PATH=os.environ["PATH"]); env.pop("INVOCATION_ID", None)
        self.log = open(os.path.join(self.tmp, "loop.out"), "w")
        self.loop = subprocess.Popen([os.path.join(self.home, "bin", "codebot"), "__loop", self.name], stdout=self.log, stderr=subprocess.STDOUT, env=env, stdin=subprocess.DEVNULL)
    def cli(self, *a): return subprocess.run([os.path.join(self.home, "bin", "codebot"), *a], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    def wait_for(self, pred, timeout=90):
        end = time.time() + timeout
        while time.time() < end:
            if pred(): return True
            time.sleep(0.3)
        return False
    def tearDown(self):
        if self.loop and self.loop.poll() is None: self.loop.kill(); self.loop.wait()
        if getattr(self, "log", None): self.log.close()
        self.ntfy.stop(); super().tearDown()
    def data(self): return T.TaskStore(self.proj()).load()
    def titles_have(self, s): return any(s in t for t in self.ntfy.titles())

class TestSupervisor(LoopBase):
    def test_block_ask_answer_complete(self):
        self.start_loop()
        self.assertTrue(self.wait_for(lambda: self.titles_have("needs you (T2)")), self.ntfy.titles())
        blocked = [m for m in self.ntfy.msgs if "needs you" in m["title"]][0]
        self.assertEqual(blocked["prio"], "urgent"); self.assertIn("failed through the whole ladder", blocked["body"])
        self.assertTrue(self.wait_for(lambda: self.titles_have("waiting on you")), "run should idle, not exit")
        self.assertIsNone(self.loop.poll())                                         # loop still alive while waiting for the human
        with open(os.path.join(self.state, "fixable"), "w") as f: f.write("1")      # the human's answer makes it solvable
        r = self.cli("answer", self.name, "T2: write yo"); self.assertIn("T2 unblocked", r.stdout, r.stdout + r.stderr)
        self.assertTrue(self.wait_for(lambda: self.titles_have("COMPLETE")), self.ntfy.titles())
        self.assertTrue(self.wait_for(lambda: self.loop.poll() is not None)); self.assertEqual(self.loop.returncode, 0)
        done = [m for m in self.ntfy.msgs if "COMPLETE" in m["title"]][0]["body"]
        self.assertIn("2 of 2 tasks verified by their own locked checks", done)
        self.assertEqual([t["status"] for t in self.data()["tasks"]], ["done", "done"])
        self.assertFalse(os.path.exists(os.path.join(self.state, ".active")))
        reps = [m for m in self.ntfy.msgs if "morning report" in m["title"]]
        self.assertGreaterEqual(len(reps), 2, self.ntfy.titles())                                  # once when it went idle waiting on you, once at the end
        self.assertIn("1 blocked and waiting on you", reps[0]["body"]); self.assertIn("BLOCKED T2:", reps[0]["body"]); self.assertIn("2/2 tasks verified", reps[-1]["body"])
        self.assertTrue(any(f.startswith("report-") for f in os.listdir(self.state)))
        eg = [json.loads(l) for l in open(os.path.join(self.home, "logs", "egress.jsonl"))]
        self.assertTrue(any(e["purpose"] == "notification" and e["dest"].startswith("127.0.0.1:") and e["project"] == self.name for e in eg))
        self.assertTrue(any(e["purpose"] == "phone-poll" for e in eg))

    def test_ntfy_can_be_disabled_entirely(self):
        with open(os.path.join(self.home, "config", "codebot.env"), "a") as f: f.write("NTFY_ENABLED=0\nPHONE_CONTROL=0\n")
        with open(os.path.join(self.state, "fixable"), "w") as f: f.write("1")
        self.start_loop(); self.assertTrue(self.wait_for(lambda: self.loop.poll() is not None, 60)); self.assertEqual(self.loop.returncode, 0)
        self.assertEqual((self.ntfy.msgs, self.ntfy.gets), ([], []))                                  # not one request left the process
        self.assertEqual([t["status"] for t in self.data()["tasks"]], ["done", "done"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "logs", "egress.jsonl")))

    def test_pause_stops_stepping_and_resume_continues(self):
        with open(os.path.join(self.state, "fixable"), "w") as f: f.write("1")
        open(os.path.join(self.state, "PAUSE"), "w").close()
        self.start_loop(); time.sleep(4)
        self.assertEqual([t["status"] for t in self.data()["tasks"]], ["pending", "pending"])        # nothing ran while paused
        os.remove(os.path.join(self.state, "PAUSE"))
        self.assertTrue(self.wait_for(lambda: self.titles_have("COMPLETE")), self.ntfy.titles())

    def test_tamper_is_pushed(self):
        self.set_env(WORKER_CMD=self.worker(WORKER + "\nif t == 'T1':\n    open(os.path.join(os.environ['CODEBOT_CHECKS'], 'T1.sh'), 'w').write('exit 0')\n"))
        self.start_loop(); self.assertTrue(self.wait_for(lambda: self.titles_have("tamper detected")), self.ntfy.titles())

    def test_budget_stop_is_reported(self):
        self.set_env(MAX_ITERATIONS="1", WORKER_CMD=self.worker("pass"))
        self.start_loop(); self.assertTrue(self.wait_for(lambda: self.titles_have("stopped by a budget")), self.ntfy.titles())
        self.assertTrue(self.wait_for(lambda: self.loop.poll() is not None))

if __name__ == "__main__": unittest.main()
