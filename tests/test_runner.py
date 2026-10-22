"""The window runs a project's program in its own terminal, including one that also opens a window."""
import os, time
import helpers
from codebot.gui import runner

class RunnerCase(helpers.Sandboxed):
    def tearDown(self):
        runner.stop(os.path.realpath(self.home), self.name)
        super().tearDown()

    def test_a_windowed_program_can_still_be_run(self):
        self.write_code("main.py", "import tkinter as tk\nprint('hi')\n")
        found = runner.command(self.code)
        self.assertEqual(found[1], "python3 main.py")
        self.assertTrue(found[2])
        self.assertTrue(runner.describe(os.path.realpath(self.home), self.name)["window"])

    def test_a_terminal_program_reads_a_line(self):
        self.write_code("main.py", "print('ready', flush=True)\nprint('got ' + input(), flush=True)\n")
        home = os.path.realpath(self.home)
        self.assertEqual(runner.command(self.code)[1], "python3 main.py")
        self.assertTrue(runner.describe(home, self.name)["show"])
        runner.start(home, self.name)
        text = self.wait(home, "ready")
        runner.send(home, self.name, "egg")
        text = self.wait(home, "got egg")
        self.assertIn("ended", text)
        self.assertFalse(runner.output(home, self.name, 0)["running"])

    def test_stop_ends_it(self):
        self.write_code("main.py", "import time\ntime.sleep(30)\n")
        home = os.path.realpath(self.home)
        runner.start(home, self.name)
        self.assertTrue(self.until(home, lambda o: o["running"]))
        runner.stop(home, self.name)
        text = self.wait(home, "stopped")
        self.assertNotIn("running program", text)
        self.assertFalse(runner.output(home, self.name, 0)["running"])

    def test_output_keeps_flowing_past_the_kept_window(self):
        self.write_code("main.py", f"print('x' * {runner.KEEP + 10000}, flush=True)\nimport time; time.sleep(0.3)\nprint('LATER', flush=True)\n")
        home = os.path.realpath(self.home); runner.start(home, self.name); since, seen = 0, ""
        for _ in range(100):
            o = runner.output(home, self.name, since); seen += o["output"]; since = o["next"]
            if "LATER" in seen or (not o["running"] and not o["output"]): break
            time.sleep(0.05)
        self.assertIn("LATER", seen)

    def wait(self, home, needle):
        for _ in range(60):
            out = runner.output(home, self.name, 0)
            if needle in out["output"]: return out["output"]
            time.sleep(0.05)
        self.fail(needle + " did not appear: " + runner.output(home, self.name, 0)["output"])

    def until(self, home, pred):
        for _ in range(40):
            if pred(runner.output(home, self.name, 0)): return True
            time.sleep(0.05)
        return False
