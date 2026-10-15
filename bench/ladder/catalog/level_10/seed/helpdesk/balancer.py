"""Workload balancing: suggest (or perform) the assignment of a ticket to the agent with the least work."""
from .errors import NotFound, ValidationError


class Balancer:
    def __init__(self, store, tickets):
        self.store = store
        self.tickets = tickets

    def load(self):
        """Number of not-yet-closed tickets per active agent or admin, keyed by user id (agents without tickets count 0)."""
        counts = {u.id: 0 for u in self.store.users.values() if u.active and u.role in ("agent", "admin")}
        for ticket in self.store.tickets.values():
            if ticket.assignee_id in counts and ticket.status != "closed":
                counts[ticket.assignee_id] += 1
        return counts

    def candidates(self, exclude=()):
        """Agents ordered by workload, then by id. Agents (not admins) come before admins when the workload is equal."""
        counts = self.load()
        rows = []
        for uid, n in counts.items():
            if uid in exclude:
                continue
            user = self.store.users[uid]
            rows.append((n, 0 if user.role == "agent" else 1, uid))
        rows.sort()
        return [uid for _, _, uid in rows]

    def suggest(self, ticket_id):
        """The agent who should get this ticket (never the current assignee), or None when nobody is available."""
        ticket = self.store.ticket(ticket_id)
        current = {ticket.assignee_id} if ticket.assignee_id is not None else set()
        order = self.candidates(exclude=current)
        return order[0] if order else None

    def autoassign(self, ticket_id, actor_id):
        """Assign the ticket to the suggested agent through the normal service call. Returns the updated ticket."""
        choice = self.suggest(ticket_id)
        if choice is None:
            raise ValidationError("there is no agent available for this ticket")
        return self.tickets.assign(ticket_id, choice, actor_id)

    def rebalance_plan(self):
        """A list of (ticket_id, from_agent, to_agent) moves that would even out the load between agents, without executing anything.

        The busiest agent gives one open ticket at a time to the least busy one while the difference is larger than one ticket."""
        counts = self.load()
        owned = {uid: sorted(t.id for t in self.store.tickets.values() if t.assignee_id == uid and t.status != "closed") for uid in counts}
        plan = []
        while True:
            busiest = max(counts, key=lambda u: (counts[u], -u), default=None)
            idlest = min(counts, key=lambda u: (counts[u], u), default=None)
            if busiest is None or counts[busiest] - counts[idlest] <= 1:
                return plan
            moved = owned[busiest].pop()
            owned[idlest].append(moved)
            counts[busiest] -= 1
            counts[idlest] += 1
            plan.append((moved, busiest, idlest))
