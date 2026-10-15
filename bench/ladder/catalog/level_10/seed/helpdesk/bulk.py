"""Bulk operations on many tickets at once. Each affected ticket goes through the normal service call, so permissions and notifications apply."""
from .errors import HelpdeskError
from .utils import hours_between

class BulkService:
    def __init__(self, store, tickets, clock):
        self.store, self.tickets, self.clock = store, tickets, clock

    def stale(self, days, statuses=("waiting", "resolved")):
        """Tickets in one of `statuses` that have not been updated for more than `days` days, in id order."""
        now = self.clock(); limit = days * 24.0
        return [t for t in sorted(self.store.tickets.values(), key=lambda t: t.id) if t.status in statuses and hours_between(t.updated, now) > limit]

    def close_stale(self, days, actor_id, resolution="closed automatically: no activity"):
        """Close stale tickets (resolving 'waiting' ones first). Returns the ids that were closed; tickets that cannot be closed are skipped."""
        closed = []
        for t in self.stale(days):
            try:
                if t.status == "waiting": self.tickets.set_status(t.id, "resolved", actor_id, resolution)
                self.tickets.set_status(t.id, "closed", actor_id)
                closed.append(t.id)
            except HelpdeskError: continue
        return closed

    def reassign(self, from_user_id, to_user_id, actor_id):
        """Move every not-closed ticket of one agent to another. Returns the ids moved."""
        moved = []
        for t in sorted(self.store.tickets.values(), key=lambda t: t.id):
            if t.assignee_id == from_user_id and t.status != "closed":
                self.tickets.assign(t.id, to_user_id, actor_id); moved.append(t.id)
        return moved

    def set_priority(self, ticket_ids, priority, actor_id):
        """Set the priority of several tickets; returns the ids changed (tickets that already have it are left alone)."""
        changed = []
        for tid in ticket_ids:
            if self.store.ticket(tid).priority == priority: continue
            self.tickets.set_priority(tid, priority, actor_id); changed.append(tid)
        return changed
