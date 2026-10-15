import os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from helpdesk.app import Helpdesk

class Clock:
    def __init__(self, ts="2024-03-01T09:00:00"): self.ts = ts
    def __call__(self): return self.ts

class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hd-test-"); self.path = os.path.join(self.dir, "hd.json"); self.clock = Clock()
        self.saved = {k: os.environ.pop(k, None) for k in ("HELPDESK_FILE", "HELPDESK_NOW", "HELPDESK_USER")}
        self.hd = self.open(); self.admin = self.hd.users.create_user("Ada", "admin"); self.agent = self.hd.users.create_user("Bob", "agent"); self.cust = self.hd.users.create_user("Cy", "customer")
    def tearDown(self):
        for k, v in self.saved.items():
            if v is not None: os.environ[k] = v
        shutil.rmtree(self.dir, ignore_errors=True)
    def open(self): return Helpdesk(self.path, self.clock)
    def ticket(self, title="Printer broken", priority="normal", requester=None, **kw):
        return self.hd.tickets.create((requester or self.cust).id, title, "It does not print", priority, kw.pop("category", "general"), actor_id=(requester or self.cust).id)
