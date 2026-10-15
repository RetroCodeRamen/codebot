"""Automatic escalation: a ticket that has used most of its SLA time is raised one priority level."""
from . import config, sla
from .utils import hours_between

ORDER = config.PRIORITIES                 # low < normal < high < urgent
THRESHOLD = 0.75                          # fraction of the SLA window that must be used up

def next_priority(priority):
    """The next higher priority, or None for the highest."""
    i = ORDER.index(priority)
    return ORDER[i + 1] if i + 1 < len(ORDER) else None

def used_fraction(ticket, now):
    """How much of the ticket's SLA window has passed (0.0 at creation, 1.0 at the deadline, above 1.0 when overdue)."""
    window = hours_between(ticket.created, ticket.sla_due) if ticket.sla_due else None
    if not window: return 0.0
    return hours_between(ticket.created, now) / window

def candidates(store, now):
    """Tickets that should be escalated now, in id order. Resolved and closed tickets are never escalated."""
    out = []
    for t in sorted(store.tickets.values(), key=lambda t: t.id):
        if t.status in ("resolved", "closed"): continue
        if next_priority(t.priority) is None: continue
        if used_fraction(t, now) >= THRESHOLD: out.append(t)
    return out

class Escalator:
    def __init__(self, store, tickets, clock):
        self.store, self.tickets, self.clock = store, tickets, clock

    def run(self, actor_id):
        """Raise every candidate by one level; returns [(ticket_id, old, new)]. The SLA deadline of an escalated ticket is recomputed by set_priority."""
        done = []
        for t in candidates(self.store, self.clock()):
            old, new = t.priority, next_priority(t.priority)
            self.tickets.set_priority(t.id, new, actor_id)
            done.append((t.id, old, new))
        return done

    def preview(self):
        """What run() would do, without changing anything."""
        return [(t.id, t.priority, next_priority(t.priority)) for t in candidates(self.store, self.clock())]
