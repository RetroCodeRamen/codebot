"""Per-user notification queue stored in the data file."""
from .errors import NotFound

KINDS = ("assigned", "comment", "status")

class Notifier:
    def __init__(self, store, clock):
        self.store, self.clock = store, clock

    def notify(self, user_id, kind, ticket_id, message):
        """Queue a notification (the caller saves the store)."""
        n = {"id": self.store.new_id("notification"), "user_id": user_id, "kind": kind, "ticket_id": ticket_id, "message": message, "ts": self.clock(), "read": False}
        self.store.notifications.append(n); return n

    def unread(self, user_id):
        return [n for n in self.store.notifications if n["user_id"] == user_id and not n["read"]]

    def all_for(self, user_id):
        return [n for n in self.store.notifications if n["user_id"] == user_id]

    def mark_read(self, user_id, ids=None):
        """Mark the given notifications (default: all) of a user as read; returns how many changed."""
        count = 0
        for n in self.store.notifications:
            if n["user_id"] == user_id and not n["read"] and (ids is None or n["id"] in ids): n["read"] = True; count += 1
        if count: self.store.save()
        return count
