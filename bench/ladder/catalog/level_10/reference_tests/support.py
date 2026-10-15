import json, os, shutil, sys, tempfile, unittest
from helpdesk.app import Helpdesk

class Clock:
    def __init__(self, ts="2024-03-01T09:00:00"): self.ts = ts
    def __call__(self): return self.ts

def v1_document():
    """A data file exactly as the old (version 1) program wrote it."""
    def ticket(i, title, priority, status="open", assignee=None, **kw):
        t = {"id": i, "title": title, "description": f"description of {title}", "requester_id": 3, "assignee_id": assignee, "status": status, "priority": priority, "category": "general",
             "created": "2024-02-01T08:00:00", "updated": "2024-02-01T08:00:00", "sla_due": "2024-02-02T08:00:00", "closed_at": None, "resolution": None, "comments": [], "watchers": [], "attachments": [], "links": []}
        t.update(kw); return t
    return {"version": 1, "next": {"user": 4, "ticket": 6, "comment": 3, "notification": 2},
            "users": [{"id": 1, "name": "Ada", "role": "admin", "email": "ada@x.org", "active": True}, {"id": 2, "name": "Bob", "role": "agent", "email": "", "active": True}, {"id": 3, "name": "Cy", "role": "customer", "email": "", "active": True}],
            "tickets": [ticket(1, "Low one", "low"), ticket(2, "Normal one", "normal", "in_progress", 2, comments=[{"id": 1, "ticket_id": 2, "author_id": 2, "body": "working", "ts": "2024-02-01T09:00:00", "internal": False}], watchers=[1]),
                        ticket(3, "High one", "high", "resolved", 2, resolution="done", comments=[{"id": 2, "ticket_id": 3, "author_id": 3, "body": "thanks", "ts": "2024-02-01T10:00:00", "internal": False}]),
                        ticket(4, "Urgent one", "urgent", attachments=[{"name": "a.log", "size": 5, "content_type": "text/plain", "added_by": 3, "ts": "2024-02-01T08:30:00"}], links=[{"kind": "related", "target": 1}]),
                        ticket(5, "Closed one", "normal", "closed", 2, closed_at="2024-02-03T08:00:00", resolution="gone")],
            "notifications": [{"id": 1, "user_id": 2, "kind": "assigned", "ticket_id": 2, "message": "ticket #2 was assigned to you by Ada", "ts": "2024-02-01T08:05:00", "read": False}],
            "saved_searches": [{"user_id": 2, "name": "mine", "query": "status:open"}]}

class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hd-ref-"); self.path = os.path.join(self.dir, "hd.json"); self.clock = Clock()
        self.saved = {k: os.environ.pop(k, None) for k in ("HELPDESK_FILE", "HELPDESK_NOW", "HELPDESK_USER")}
        self.hd = self.open(); self.admin = self.hd.users.create_user("Ada", "admin"); self.agent = self.hd.users.create_user("Bob", "agent"); self.cust = self.hd.users.create_user("Cy", "customer")
    def tearDown(self):
        for k, v in self.saved.items():
            if v is not None: os.environ[k] = v
        shutil.rmtree(self.dir, ignore_errors=True)
    def open(self, path=None): return Helpdesk(path or self.path, self.clock)
    def ticket(self, title="Printer broken", priority="normal", requester=None, **kw):
        r = requester or self.cust; return self.hd.tickets.create(r.id, title, "It does not print", priority, kw.pop("category", "general"), actor_id=r.id)
    def raw(self, path=None):
        with open(path or self.path) as f: return json.load(f)
    def write_v1(self, path=None, doc=None):
        with open(path or self.path, "w") as f: json.dump(doc or v1_document(), f, indent=1)
