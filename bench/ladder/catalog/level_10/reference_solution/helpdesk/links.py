"""Links between tickets: `duplicates`, `blocks` and `related`. A link is stored on the ticket it starts from as {"kind": ..., "target": ticket_id}."""
from . import permissions
from .audit import AuditLog
from .errors import NotFound, ValidationError

KINDS = ("duplicates", "blocks", "related")
INVERSE = {"duplicates": "duplicated by", "blocks": "blocked by", "related": "related to"}

class LinkService:
    def __init__(self, store, tickets, clock, audit=None):
        self.store, self.tickets, self.clock = store, tickets, clock
        self.audit = audit or AuditLog(store, clock)

    def link(self, source_id, kind, target_id, actor_id):
        """Add a link; adding the same link twice is harmless. A ticket cannot link to itself."""
        actor = self.store.user(actor_id); self.audit.guard(actor, "change_status", "ticket.link", "ticket", source_id, permissions)
        if kind not in KINDS: raise ValidationError(f"invalid link kind '{kind}' (choose from {', '.join(KINDS)})")
        if source_id == target_id: raise ValidationError("a ticket cannot be linked to itself")
        src = self.store.ticket(source_id); self.store.ticket(target_id)
        entry = {"kind": kind, "target": target_id}
        if entry not in src.links:
            src.links.append(entry); src.updated = self.clock()
            self.audit.record(actor.id, "ticket.link", "ticket", src.id, {"links": [len(src.links) - 1, len(src.links)]}); self.store.save()
        return src

    def unlink(self, source_id, kind, target_id, actor_id):
        actor = self.store.user(actor_id); self.audit.guard(actor, "change_status", "ticket.unlink", "ticket", source_id, permissions); src = self.store.ticket(source_id)
        entry = {"kind": kind, "target": target_id}
        if entry not in src.links: raise NotFound(f"ticket #{source_id} has no '{kind}' link to #{target_id}")
        src.links.remove(entry); src.updated = self.clock()
        self.audit.record(actor.id, "ticket.unlink", "ticket", src.id, {"links": [len(src.links) + 1, len(src.links)]}); self.store.save(); return src

    def outgoing(self, ticket_id):
        return [dict(l) for l in self.store.ticket(ticket_id).links]

    def incoming(self, ticket_id):
        """Links from other tickets that point at this one, as (source_id, kind)."""
        self.store.ticket(ticket_id)
        return sorted((t.id, l["kind"]) for t in self.store.tickets.values() for l in t.links if l["target"] == ticket_id)

    def blockers(self, ticket_id):
        """Open tickets that block this one."""
        return sorted(src for src, kind in self.incoming(ticket_id) if kind == "blocks" and self.store.tickets[src].status != "closed")

    def describe(self, ticket_id):
        """Human-readable lines for `ticket show`."""
        lines = [f"{l['kind']} #{l['target']}" for l in self.outgoing(ticket_id)]
        lines += [f"{INVERSE[k]} #{src}" for src, k in self.incoming(ticket_id)]
        return lines
