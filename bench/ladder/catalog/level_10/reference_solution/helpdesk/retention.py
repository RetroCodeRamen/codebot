"""Retention: closed tickets older than a number of days are moved to an archive file and removed from the data file."""
import json, os
from . import permissions
from .audit import AuditLog
from .utils import hours_between

class Retention:
    def __init__(self, store, clock, audit=None):
        self.store, self.clock = store, clock
        self.audit = audit or AuditLog(store, clock)

    @property
    def archive_path(self):
        return self.store.path + ".archive.jsonl"

    def expired(self, days):
        """Closed tickets whose closing time is more than `days` days ago, in id order."""
        now = self.clock(); limit = days * 24.0
        return [t for t in sorted(self.store.tickets.values(), key=lambda t: t.id) if t.closed_at and hours_between(t.closed_at, now) > limit]

    def purge(self, days, actor_id):
        """Archive and remove the expired tickets; returns their ids. The archive is appended before anything is deleted."""
        self.audit.guard(self.store.user(actor_id), "purge", "ticket.purge", "ticket", None, permissions)
        victims = self.expired(days)
        if not victims: return []
        with open(self.archive_path, "a") as f:
            for t in victims: f.write(json.dumps({"archived": self.clock(), "ticket": t.to_dict()}) + "\n")
            f.flush(); os.fsync(f.fileno())
        for t in victims:
            del self.store.tickets[t.id]
            self.audit.record(actor_id, "ticket.purge", "ticket", t.id, {"status": [t.status, None]})
        self.store.save(); return [t.id for t in victims]

    def archived(self):
        """Ticket dicts that were archived so far, oldest first."""
        try:
            with open(self.archive_path) as f: return [json.loads(ln)["ticket"] for ln in f if ln.strip()]
        except OSError: return []
