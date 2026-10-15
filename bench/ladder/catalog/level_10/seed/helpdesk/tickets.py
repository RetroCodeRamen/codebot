"""Ticket operations: create, assign, comment, change status and priority, list and fetch with the visibility rules of each role."""
from . import config, permissions, sla, workflow
from .errors import NotFound, PermissionDenied, ValidationError
from .models import Comment, Ticket
from .utils import require_choice, require_text

class TicketService:
    def __init__(self, store, users, notifier, clock):
        self.store, self.users, self.notifier, self.clock = store, users, notifier, clock

    # ---- helpers ----
    def _actor(self, actor_id):
        return self.store.user(actor_id)

    def _touch(self, ticket):
        ticket.updated = self.clock()

    def _can_see(self, user, ticket):
        return permissions.can(user, "view_all") or ticket.requester_id == user.id

    def visible_comments(self, ticket, user):
        """Customers do not see internal notes."""
        return [c for c in ticket.comments if not c.internal or permissions.can(user, "internal_comment")]

    # ---- creation ----
    def create(self, requester_id, title, description, priority="normal", category="general", actor_id=None):
        actor = self._actor(actor_id if actor_id is not None else requester_id); requester = self.store.user(requester_id)
        permissions.require(actor, "create_ticket")
        if actor.id != requester.id and not permissions.can(actor, "view_all"): raise PermissionDenied(f"{actor.name} may only create tickets for themselves")
        if not requester.active: raise ValidationError(f"{requester.name} is disabled")
        title = require_text(title, "title", config.TITLE_MAX); description = require_text(description, "description", config.BODY_MAX)
        require_choice(priority, "priority", config.PRIORITIES); require_choice(category, "category", config.CATEGORIES)
        now = self.clock()
        t = Ticket(self.store.new_id("ticket"), title, description, requester.id, None, "open", priority, category, now, now, sla.due_for(priority, now))
        self.store.tickets[t.id] = t; self.store.save(); return t

    # ---- reading ----
    def get(self, ticket_id, actor_id=None):
        t = self.store.ticket(ticket_id)
        if actor_id is not None and not self._can_see(self._actor(actor_id), t): raise NotFound(f"no ticket with id {ticket_id}")
        return t

    def list(self, actor_id, status=None, assignee_id=None, priority=None, category=None, requester_id=None):
        user = self._actor(actor_id)
        out = []
        for t in sorted(self.store.tickets.values(), key=lambda t: t.id):
            if not self._can_see(user, t): continue
            if status is not None and t.status != status: continue
            if assignee_id is not None and t.assignee_id != assignee_id: continue
            if priority is not None and t.priority != priority: continue
            if category is not None and t.category != category: continue
            if requester_id is not None and t.requester_id != requester_id: continue
            out.append(t)
        return out

    # ---- changes ----
    def assign(self, ticket_id, assignee_id, actor_id):
        actor = self._actor(actor_id); permissions.require(actor, "assign"); t = self.store.ticket(ticket_id); assignee = self.store.user(assignee_id)
        if assignee.role not in ("agent", "admin") or not assignee.active: raise ValidationError(f"{assignee.name} cannot be assigned tickets")
        if t.status == "closed": raise workflow.InvalidTransition(f"ticket #{t.id} is closed")
        t.assignee_id = assignee.id
        if t.status == "open": t.status = "in_progress"
        self._touch(t)
        if assignee.id != actor.id: self.notifier.notify(assignee.id, "assigned", t.id, f"ticket #{t.id} was assigned to you by {actor.name}")
        self.store.save(); return t

    def set_status(self, ticket_id, status, actor_id, resolution=None):
        actor = self._actor(actor_id); permissions.require(actor, "change_status"); t = self.store.ticket(ticket_id)
        require_choice(status, "status", config.STATUSES); workflow.check_transition(t, status, resolution)
        t.status = status
        if status == "resolved": t.resolution = resolution.strip()
        if status == "closed": t.closed_at = self.clock()
        if status == "open": t.closed_at = None; t.resolution = None
        self._touch(t)
        if status in ("resolved", "closed") and t.requester_id != actor.id: self.notifier.notify(t.requester_id, "status", t.id, f"ticket #{t.id} is now {status}")
        for uid in t.watchers:
            if uid not in (actor.id, t.requester_id): self.notifier.notify(uid, "status", t.id, f"ticket #{t.id} is now {status}")
        self.store.save(); return t

    def set_priority(self, ticket_id, priority, actor_id):
        actor = self._actor(actor_id); permissions.require(actor, "change_priority"); t = self.store.ticket(ticket_id); require_choice(priority, "priority", config.PRIORITIES)
        t.priority = priority; t.sla_due = sla.due_for(priority, t.created); self._touch(t); self.store.save(); return t

    def add_comment(self, ticket_id, author_id, body, internal=False):
        author = self._actor(author_id); permissions.require(author, "comment"); t = self.get(ticket_id, author_id)
        body = require_text(body, "comment", config.BODY_MAX)
        if t.status == "closed": raise ValidationError(f"ticket #{t.id} is closed")
        if internal: permissions.require(author, "internal_comment")
        c = Comment(self.store.new_id("comment"), t.id, author.id, body, self.clock(), bool(internal))
        t.comments.append(c); self._touch(t)
        for uid in ({t.requester_id, t.assignee_id} | set(t.watchers)) - {None, author.id}:
            if internal and not permissions.can(self.store.user(uid), "internal_comment"): continue
            self.notifier.notify(uid, "comment", t.id, f"{author.name} commented on ticket #{t.id}")
        self.store.save(); return c

    def watch(self, ticket_id, user_id, actor_id):
        """Subscribe a user to a ticket's comments and status changes. Customers can only watch tickets they can see; watching twice is harmless."""
        actor = self._actor(actor_id); permissions.require(actor, "watch"); t = self.get(ticket_id, actor_id); target = self.store.user(user_id)
        if target.id != actor.id and not permissions.can(actor, "view_all"): raise PermissionDenied(f"{actor.name} may only subscribe themselves")
        if target.id not in t.watchers: t.watchers.append(target.id); self._touch(t); self.store.save()
        return t

    def unwatch(self, ticket_id, user_id, actor_id):
        actor = self._actor(actor_id); permissions.require(actor, "watch"); t = self.get(ticket_id, actor_id)
        if user_id != actor.id and not permissions.can(actor, "view_all"): raise PermissionDenied(f"{actor.name} may only unsubscribe themselves")
        if user_id in t.watchers: t.watchers.remove(user_id); self._touch(t); self.store.save()
        return t

    def add_attachment(self, ticket_id, name, size, content_type, actor_id):
        """Record attachment metadata (the file itself is stored elsewhere)."""
        actor = self._actor(actor_id); permissions.require(actor, "attach"); t = self.get(ticket_id, actor_id)
        name = require_text(name, "attachment name", 120)
        if "/" in name or "\\" in name: raise ValidationError("attachment name must not contain a path")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= 10 * 1024 * 1024: raise ValidationError("attachment size must be between 1 byte and 10 MiB")
        if t.status == "closed": raise ValidationError(f"ticket #{t.id} is closed")
        if len(t.attachments) >= 10: raise ValidationError("a ticket can have at most 10 attachments")
        a = {"name": name, "size": size, "content_type": require_text(content_type, "content type", 80), "added_by": actor.id, "ts": self.clock()}
        t.attachments.append(a); self._touch(t); self.store.save(); return a

    def reopen(self, ticket_id, actor_id):
        return self.set_status(ticket_id, "open", actor_id)
