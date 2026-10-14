"""Test helpers: throwaway CODEBOT_HOME + project + code dir. Never touches the real projects."""
import os, shutil, subprocess, sys, tempfile, textwrap, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))
from codebot.config import Project

class Sandboxed(unittest.TestCase):
    name = "t1"
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cbtest-")
        self.home = os.path.join(self.tmp, "home"); self.code = os.path.join(self.tmp, "code")
        os.makedirs(os.path.join(self.home, "config")); os.makedirs(self.code)
        with open(os.path.join(self.home, "config", "codebot.env"), "w") as f: f.write('ITER_TIMEOUT=60\nCHECK_TIMEOUT=30\n')
        self.state = os.path.join(self.home, "projects", self.name); os.makedirs(self.state)
        with open(os.path.join(self.state, "project.env"), "w") as f: f.write(f'WORKDIR="{self.code}"\n')
        subprocess.run(["git", "init", "-q", "-b", "main", self.code], check=True)
        subprocess.run(["git", "-C", self.code, "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", self.code, "config", "user.name", "t"], check=True)
        self.write_code("README.md", "base\n")
        subprocess.run(["git", "-C", self.code, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.code, "commit", "-qm", "init"], check=True)
    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)
    def proj(self): return Project(self.name, self.home)
    def write_plan(self, text):
        with open(os.path.join(self.state, "PLAN.md"), "w") as f: f.write(textwrap.dedent(text).lstrip())
    def read(self, rel):
        with open(os.path.join(self.state, rel)) as f: return f.read()
    def write_code(self, rel, text):
        p = os.path.join(self.code, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f: f.write(text)
    def set_env(self, **kv):
        with open(os.path.join(self.state, "project.env"), "a") as f:
            for k, v in kv.items(): f.write(f'{k}="{v}"\n')
    def worker(self, script):
        """script: python source run in the code dir as the scripted 'model'. Returns the WORKER_CMD string."""
        path = os.path.join(self.tmp, "worker.py")
        with open(path, "w") as f: f.write(textwrap.dedent(script))
        return f"{sys.executable} {path}"

PLAN2 = """
# Goal
Test.
# Definition of done
Both files exist.
# Tasks
- [ ] 1. Create a.txt containing hi
  - check: `grep -qx hi a.txt`
  - files: a.txt
- [ ] 2. Create b.txt containing yo
  - check: `grep -qx yo b.txt`
  - size: S
  - deps: 1
"""
