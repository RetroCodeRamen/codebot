import os, shutil, tempfile, unittest
from tracker import Tracker

class Clock:
    def __init__(self, ts="2024-03-01T09:00:00"): self.ts = ts
    def __call__(self): return self.ts

class Base(unittest.TestCase):
    """root (admin); alice manager, bob developer, carol reporter of the PUBLIC project WEB; dave is a global viewer, eve a member without a role; the PRIVATE project SEC has only alice (manager)."""
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="trk-"); self.path = os.path.join(self.dir, "t.db"); self.clock = Clock(); self.t = self.open(); self.addCleanup(self.t.close)
        t = self.t; t.create_user("root", "password1", "admin")
        for n in ("alice", "bob", "carol", "eve"): t.create_user(n, "password1", "member", actor="root")
        t.create_user("dave", "password1", "viewer", actor="root")
        t.create_project("WEB", "Website", "root", public=True); t.create_project("SEC", "Security", "root")
        for n, r in (("alice", "manager"), ("bob", "developer"), ("carol", "reporter")): t.add_member("WEB", n, r, "root")
        t.add_member("SEC", "alice", "manager", "root"); t.add_member("WEB", "dave", "developer", "root")
    def tearDown(self): shutil.rmtree(self.dir, ignore_errors=True)
    def open(self, path=None):
        t = Tracker(path or self.path, self.clock); return t
    def issue(self, title="Broken login", actor="bob", project="WEB", **kw): return self.t.create_issue(project, title, kw.pop("description", "details"), actor, **kw)
