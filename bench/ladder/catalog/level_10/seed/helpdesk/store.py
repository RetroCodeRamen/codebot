"""The data file. One JSON document holds everything; services load it once, mutate the in-memory objects and call save()."""
import json, os, tempfile
from .errors import NotFound, StoreError
from .models import Comment, Ticket, User

VERSION = 1

class Store:
    def __init__(self, path):
        self.path = path
        self.users, self.tickets, self.notifications, self.saved_searches = {}, {}, [], []
        self.next = {"user": 1, "ticket": 1, "comment": 1, "notification": 1}
        if os.path.exists(path): self._load()

    # ---- persistence ----
    def _load(self):
        try:
            with open(self.path) as f: data = json.load(f)
        except (OSError, ValueError) as e: raise StoreError(f"cannot read {self.path}: {e}")
        if not isinstance(data, dict) or "version" not in data: raise StoreError(f"{self.path} is not a helpdesk data file")
        if data["version"] != VERSION: raise StoreError(f"unsupported data version {data['version']} (this program reads version {VERSION})")
        try:
            self.users = {u["id"]: User.from_dict(u) for u in data["users"]}
            self.tickets = {t["id"]: Ticket.from_dict(t) for t in data["tickets"]}
            self.notifications = list(data.get("notifications", []))
            self.saved_searches = [dict(s) for s in data.get("saved_searches", [])]
            self.next = {**self.next, **data["next"]}
        except (KeyError, TypeError, ValueError) as e: raise StoreError(f"{self.path} is damaged: {e!r}")

    def document(self):
        return {"version": VERSION, "next": dict(self.next), "users": [u.to_dict() for u in self.users.values()],
                "tickets": [t.to_dict() for t in sorted(self.tickets.values(), key=lambda t: t.id)], "notifications": list(self.notifications), "saved_searches": [dict(s) for s in self.saved_searches]}

    def save(self):
        """Write the whole document atomically (temp file in the same directory, then rename)."""
        d = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".helpdesk-")
        with os.fdopen(fd, "w") as f: json.dump(self.document(), f, indent=1)
        os.replace(tmp, self.path)

    # ---- ids and lookups ----
    def new_id(self, kind):
        n = self.next[kind]; self.next[kind] = n + 1; return n

    def user(self, user_id):
        if user_id not in self.users: raise NotFound(f"no user with id {user_id}")
        return self.users[user_id]

    def ticket(self, ticket_id):
        if ticket_id not in self.tickets: raise NotFound(f"no ticket with id {ticket_id}")
        return self.tickets[ticket_id]

    def comment(self, comment_id):
        for t in self.tickets.values():
            for c in t.comments:
                if c.id == comment_id: return c
        raise NotFound(f"no comment with id {comment_id}")
