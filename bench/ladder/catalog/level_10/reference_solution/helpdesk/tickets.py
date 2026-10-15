"""Ticket operations: create, assign, comment, change status and severity, labels, merge, watchers, attachments, listing with the visibility rules of each role."""
import re

from . import config, permissions, sla, workflow
from .audit import AuditLog
from .errors import NotFound, PermissionDenied, ValidationError
from .models import Comment, Ticket
from .utils import require_choice, require_text, system_clock

LABEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")


class TicketService:
    def __init__(self, store, users, notifier, clock, audit=None):
        self.store, self.users, self.notifier, self.clock = store, users, notifier, clock
        self.audit = audit or AuditLog(store, clock or system_clock)

    # ---- helpers ----
    def _actor(self, actor_id):
        return self.store.user(actor_id)

    def _touch(self, ticket):
        ticket.updated = self.clock()

    def _can_see(self, user, ticket):
        return permissions.can(user, "view_all") or ticket.requester_id == user.id

    def _require(self, actor, permission, action, ticket_id=None):
        """Check a permission; a refusal is written to the audit log (outcome 'denied') before PermissionDenied propagates."""
        self.audit.guard(actor, permission, action, "ticket", ticket_id, permissions)

    def _deny(self, actor, action, ticket_id, message):
        error = PermissionDenied(message)
        self.audit.deny(actor.id, action, "ticket", ticket_id, error)
        raise error

    def visible_comments(self, ticket, user):
        """Customers do not see internal notes."""
        return [c for c in ticket.comments if not c.internal or permissions.can(user, "internal_comment")]

    # ---- creation ----
    def create(self, requester_id, title, description, priority="normal", category="general", actor_id=None, severity=None):
        actor = self._actor(actor_id if actor_id is not None else requester_id)
        requester = self.store.user(requester_id)
        self._require(actor, "create_ticket", "ticket.create")
        if actor.id != requester.id and not permissions.can(actor, "view_all"):
            self._deny(actor, "ticket.create", None, f"{actor.name} may only create tickets for themselves")
        if not requester.active:
            raise ValidationError(f"{requester.name} is disabled")
        title = require_text(title, "title", config.TITLE_MAX)
        description = require_text(description, "description", config.BODY_MAX)
        require_choice(priority, "priority", config.PRIORITIES)
        if severity is not None:
            require_choice(severity, "severity", config.SEVERITIES)
            if priority != "normal" and config.PRIORITY_TO_SEVERITY[priority] != severity:
                raise ValidationError(f"priority '{priority}' and severity '{severity}' disagree")
        else:
            severity = config.PRIORITY_TO_SEVERITY[priority]
        require_choice(category, "category", config.CATEGORIES)
        now = self.clock()
        t = Ticket(self.store.new_id("ticket"), title, description, requester.id, None, "open", None, category, now, now, sla.due_for_severity(severity, now), severity=severity)
        self.store.tickets[t.id] = t
        self.audit.record(actor.id, "ticket.create", "ticket", t.id, {"title": [None, title], "severity": [None, severity], "category": [None, category], "requester_id": [None, requester.id]})
        self.store.save()
        return t

    # ---- reading ----
    def get(self, ticket_id, actor_id=None):
        t = self.store.ticket(ticket_id)
        if actor_id is not None and not self._can_see(self._actor(actor_id), t):
            raise NotFound(f"no ticket with id {ticket_id}")
        return t

    def list(self, actor_id, status=None, assignee_id=None, priority=None, category=None, requester_id=None, severity=None, label=None):
        user = self._actor(actor_id)
        out = []
        for t in sorted(self.store.tickets.values(), key=lambda t: t.id):
            if not self._can_see(user, t):
                continue
            if status is not None and t.status != status:
                continue
            if assignee_id is not None and t.assignee_id != assignee_id:
                continue
            if priority is not None and t.priority != priority:
                continue
            if severity is not None and t.severity != severity:
                continue
            if label is not None and label.lower() not in t.labels:
                continue
            if category is not None and t.category != category:
                continue
            if requester_id is not None and t.requester_id != requester_id:
                continue
            out.append(t)
        return out

    # ---- changes ----
    def assign(self, ticket_id, assignee_id, actor_id):
        actor = self._actor(actor_id)
        self._require(actor, "assign", "ticket.assign", ticket_id)
        t = self.store.ticket(ticket_id)
        assignee = self.store.user(assignee_id)
        if assignee.role not in ("agent", "admin") or not assignee.active:
            raise ValidationError(f"{assignee.name} cannot be assigned tickets")
        if t.status == "closed":
            raise workflow.InvalidTransition(f"ticket #{t.id} is closed")
        before = {"assignee_id": t.assignee_id, "status": t.status}
        t.assignee_id = assignee.id
        if t.status == "open":
            t.status = "in_progress"
        self._touch(t)
        if assignee.id != actor.id:
            self.notifier.notify(assignee.id, "assigned", t.id, f"ticket #{t.id} was assigned to you by {actor.name}")
        changes = {k: [v, getattr(t, k)] for k, v in before.items() if v != getattr(t, k)}
        if changes:
            self.audit.record(actor.id, "ticket.assign", "ticket", t.id, changes)
        self.store.save()
        return t

    def set_status(self, ticket_id, status, actor_id, resolution=None):
        actor = self._actor(actor_id)
        self._require(actor, "change_status", "ticket.status", ticket_id)
        t = self.store.ticket(ticket_id)
        require_choice(status, "status", config.STATUSES)
        workflow.check_transition(t, status, resolution)
        before = {"status": t.status, "resolution": t.resolution}
        t.status = status
        if status == "resolved":
            t.resolution = resolution.strip()
        if status == "closed":
            t.closed_at = self.clock()
        if status == "open":
            t.closed_at = None
            t.resolution = None
        self._touch(t)
        if status in ("resolved", "closed") and t.requester_id != actor.id:
            self.notifier.notify(t.requester_id, "status", t.id, f"ticket #{t.id} is now {status}")
        for uid in t.watchers:
            if uid not in (actor.id, t.requester_id):
                self.notifier.notify(uid, "status", t.id, f"ticket #{t.id} is now {status}")
        self.audit.record(actor.id, "ticket.status", "ticket", t.id, {k: [v, getattr(t, k)] for k, v in before.items() if v != getattr(t, k)})
        self.store.save()
        return t

    def set_severity(self, ticket_id, severity, actor_id):
        actor = self._actor(actor_id)
        self._require(actor, "change_priority", "ticket.severity", ticket_id)
        t = self.store.ticket(ticket_id)
        require_choice(severity, "severity", config.SEVERITIES)
        before = t.severity
        t.severity = severity
        t.sla_due = sla.due_for_severity(severity, t.created)
        self._touch(t)
        if before != severity:
            self.audit.record(actor.id, "ticket.severity", "ticket", t.id, {"severity": [before, severity]})
        self.store.save()
        return t

    def set_priority(self, ticket_id, priority, actor_id):
        """The old name of set_severity: takes priority names (low, normal, high, urgent)."""
        actor = self._actor(actor_id)
        self._require(actor, "change_priority", "ticket.severity", ticket_id)
        self.store.ticket(ticket_id)
        require_choice(priority, "priority", config.PRIORITIES)
        return self.set_severity(ticket_id, config.PRIORITY_TO_SEVERITY[priority], actor_id)

    def add_comment(self, ticket_id, author_id, body, internal=False):
        author = self._actor(author_id)
        self._require(author, "comment", "ticket.comment", ticket_id)
        t = self.get(ticket_id, author_id)
        body = require_text(body, "comment", config.BODY_MAX)
        if t.status == "closed":
            raise ValidationError(f"ticket #{t.id} is closed")
        if internal:
            self._require(author, "internal_comment", "ticket.comment", ticket_id)
        c = Comment(self.store.new_id("comment"), t.id, author.id, body, self.clock(), bool(internal))
        t.comments.append(c)
        self._touch(t)
        for uid in ({t.requester_id, t.assignee_id} | set(t.watchers)) - {None, author.id}:
            if internal and not permissions.can(self.store.user(uid), "internal_comment"):
                continue
            self.notifier.notify(uid, "comment", t.id, f"{author.name} commented on ticket #{t.id}")
        self.audit.record(author.id, "ticket.comment", "ticket", t.id, {"comment_id": [None, c.id], "internal": [None, c.internal]})
        self.store.save()
        return c

    def watch(self, ticket_id, user_id, actor_id):
        """Subscribe a user to a ticket's comments and status changes. Customers can only watch tickets they can see; watching twice is harmless."""
        actor = self._actor(actor_id)
        self._require(actor, "watch", "ticket.watch", ticket_id)
        t = self.get(ticket_id, actor_id)
        target = self.store.user(user_id)
        if target.id != actor.id and not permissions.can(actor, "view_all"):
            self._deny(actor, "ticket.watch", t.id, f"{actor.name} may only subscribe themselves")
        if target.id not in t.watchers:
            before = list(t.watchers)
            t.watchers.append(target.id)
            self._touch(t)
            self.audit.record(actor.id, "ticket.watch", "ticket", t.id, {"watchers": [before, list(t.watchers)]})
            self.store.save()
        return t

    def unwatch(self, ticket_id, user_id, actor_id):
        actor = self._actor(actor_id)
        self._require(actor, "watch", "ticket.unwatch", ticket_id)
        t = self.get(ticket_id, actor_id)
        if user_id != actor.id and not permissions.can(actor, "view_all"):
            self._deny(actor, "ticket.unwatch", t.id, f"{actor.name} may only unsubscribe themselves")
        if user_id in t.watchers:
            before = list(t.watchers)
            t.watchers.remove(user_id)
            self._touch(t)
            self.audit.record(actor.id, "ticket.unwatch", "ticket", t.id, {"watchers": [before, list(t.watchers)]})
            self.store.save()
        return t

    def add_attachment(self, ticket_id, name, size, content_type, actor_id):
        """Record attachment metadata (the file itself is stored elsewhere)."""
        actor = self._actor(actor_id)
        self._require(actor, "attach", "ticket.attach", ticket_id)
        t = self.get(ticket_id, actor_id)
        name = require_text(name, "attachment name", 120)
        if "/" in name or "\\" in name:
            raise ValidationError("attachment name must not contain a path")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= 10 * 1024 * 1024:
            raise ValidationError("attachment size must be between 1 byte and 10 MiB")
        if t.status == "closed":
            raise ValidationError(f"ticket #{t.id} is closed")
        if len(t.attachments) >= 10:
            raise ValidationError("a ticket can have at most 10 attachments")
        a = {"name": name, "size": size, "content_type": require_text(content_type, "content type", 80), "added_by": actor.id, "ts": self.clock()}
        t.attachments.append(a)
        self._touch(t)
        self.audit.record(actor.id, "ticket.attach", "ticket", t.id, {"attachments": [len(t.attachments) - 1, len(t.attachments)]})
        self.store.save()
        return a

    # ---- labels ----
    def add_label(self, ticket_id, label, actor_id):
        actor = self._actor(actor_id)
        self._require(actor, "label", "ticket.label_add", ticket_id)
        t = self.store.ticket(ticket_id)
        label = require_text(label, "label", 24).lower()
        if not LABEL.match(label):
            raise ValidationError(f"invalid label '{label}' (use a-z, 0-9 and hyphens, starting with a letter or digit)")
        if label in t.labels:
            return t
        if len(t.labels) >= config.MAX_LABELS:
            raise ValidationError(f"a ticket can have at most {config.MAX_LABELS} labels")
        before = list(t.labels)
        t.labels.append(label)
        self._touch(t)
        self.audit.record(actor.id, "ticket.label_add", "ticket", t.id, {"labels": [before, list(t.labels)]})
        self.store.save()
        return t

    def remove_label(self, ticket_id, label, actor_id):
        actor = self._actor(actor_id)
        self._require(actor, "label", "ticket.label_remove", ticket_id)
        t = self.store.ticket(ticket_id)
        label = str(label).strip().lower()
        if label not in t.labels:
            raise NotFound(f"ticket #{t.id} has no label '{label}'")
        before = list(t.labels)
        t.labels.remove(label)
        self._touch(t)
        self.audit.record(actor.id, "ticket.label_remove", "ticket", t.id, {"labels": [before, list(t.labels)]})
        self.store.save()
        return t

    # ---- merge ----
    def merge(self, source_id, target_id, actor_id):
        """Merge a duplicate ticket into another: comments, labels and watchers move to the target, links that pointed at the source are redirected,
        and the source is closed with `merged_into` set. Returns the number of comments moved."""
        actor = self._actor(actor_id)
        self._require(actor, "merge", "ticket.merge", source_id)
        if source_id == target_id:
            raise ValidationError("a ticket cannot be merged into itself")
        src, dst = self.store.ticket(source_id), self.store.ticket(target_id)
        if src.status == "closed":
            raise ValidationError(f"ticket #{src.id} is closed" + (f" (it was merged into #{src.merged_into})" if src.merged_into else ""))
        if dst.merged_into is not None:
            raise ValidationError(f"ticket #{dst.id} was merged into #{dst.merged_into}")
        if dst.status == "closed":
            raise ValidationError(f"ticket #{dst.id} is closed")
        moved = len(src.comments)
        for c in src.comments:
            c.ticket_id = dst.id
        dst.comments.extend(src.comments)
        dst.comments.sort(key=lambda c: (c.ts, c.id))
        src.comments = []
        for label in src.labels:
            if label not in dst.labels and len(dst.labels) < config.MAX_LABELS:
                dst.labels.append(label)
        for uid in src.watchers:
            if uid not in dst.watchers:
                dst.watchers.append(uid)
        for other in self.store.tickets.values():
            if other.id in (src.id, dst.id):
                continue
            redirected = []
            for link in other.links:
                entry = {"kind": link["kind"], "target": dst.id if link["target"] == src.id else link["target"]}
                if entry not in redirected:
                    redirected.append(entry)
            other.links = redirected
        dst.links = [l for l in dst.links if l["target"] != src.id]
        src.links = []
        old_status, now = src.status, self.clock()
        src.status, src.closed_at, src.resolution, src.merged_into = "closed", now, f"merged into #{dst.id}", dst.id
        src.updated = dst.updated = now
        if src.assignee_id is not None and src.assignee_id != actor.id:
            self.notifier.notify(src.assignee_id, "status", src.id, f"ticket #{src.id} was merged into #{dst.id}")
        self.audit.record(actor.id, "ticket.merge", "ticket", src.id, {"merged_into": [None, dst.id], "status": [old_status, "closed"], "comments_moved": [0, moved]})
        self.store.save()
        return moved

    def reopen(self, ticket_id, actor_id):
        return self.set_status(ticket_id, "open", actor_id)
