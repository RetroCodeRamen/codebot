from base import Base
from helpdesk.errors import InvalidTransition, NotFound, PermissionDenied, ValidationError

class TestTickets(Base):
    def test_create_sets_defaults_and_sla(self):
        t = self.ticket(priority="high"); self.assertEqual((t.id, t.status, t.assignee_id, t.priority, t.created, t.sla_due), (1, "open", None, "high", "2024-03-01T09:00:00", "2024-03-01T17:00:00"))
    def test_validation(self):
        for kw in ({"title": " "}, {"description": ""}, {"priority": "asap"}, {"category": "other"}):
            args = {"title": "T", "description": "d", "priority": "normal", "category": "general", **kw}
            with self.assertRaises(ValidationError): self.hd.tickets.create(self.cust.id, args["title"], args["description"], args["priority"], args["category"])
    def test_customers_create_only_for_themselves(self):
        with self.assertRaises(PermissionDenied): self.hd.tickets.create(self.agent.id, "T", "d", actor_id=self.cust.id)
        t = self.hd.tickets.create(self.cust.id, "T", "d", actor_id=self.agent.id); self.assertEqual(t.requester_id, self.cust.id)
    def test_assign_moves_open_to_in_progress_and_notifies(self):
        t = self.ticket(); self.hd.tickets.assign(t.id, self.agent.id, self.admin.id); t = self.hd.tickets.get(t.id); self.assertEqual((t.assignee_id, t.status), (self.agent.id, "in_progress")); self.assertEqual(len(self.hd.notifier.unread(self.agent.id)), 1)
    def test_assign_to_a_customer_is_refused(self):
        with self.assertRaises(ValidationError): self.hd.tickets.assign(self.ticket().id, self.cust.id, self.admin.id)
    def test_customers_cannot_assign(self):
        with self.assertRaises(PermissionDenied): self.hd.tickets.assign(self.ticket().id, self.agent.id, self.cust.id)
    def test_status_flow_with_resolution(self):
        t = self.ticket()
        with self.assertRaises(ValidationError): self.hd.tickets.set_status(t.id, "resolved", self.agent.id)
        self.hd.tickets.set_status(t.id, "resolved", self.agent.id, "replaced the toner"); self.assertEqual(self.hd.tickets.get(t.id).resolution, "replaced the toner")
        self.clock.ts = "2024-03-02T10:00:00"; self.hd.tickets.set_status(t.id, "closed", self.agent.id); self.assertEqual(self.hd.tickets.get(t.id).closed_at, "2024-03-02T10:00:00")
    def test_invalid_transitions(self):
        t = self.ticket()
        with self.assertRaises(InvalidTransition): self.hd.tickets.set_status(t.id, "open", self.agent.id)
        self.hd.tickets.set_status(t.id, "closed", self.agent.id)
        with self.assertRaises(InvalidTransition): self.hd.tickets.set_status(t.id, "waiting", self.agent.id)
    def test_reopen_clears_closing_data(self):
        t = self.ticket(); self.hd.tickets.set_status(t.id, "resolved", self.agent.id, "done"); self.hd.tickets.reopen(t.id, self.agent.id); t = self.hd.tickets.get(t.id); self.assertEqual((t.status, t.resolution, t.closed_at), ("open", None, None))
    def test_priority_change_recomputes_sla(self):
        t = self.ticket(priority="low"); self.hd.tickets.set_priority(t.id, "urgent", self.agent.id); self.assertEqual(self.hd.tickets.get(t.id).sla_due, "2024-03-01T13:00:00")
    def test_customers_cannot_change_priority_or_status(self):
        t = self.ticket()
        with self.assertRaises(PermissionDenied): self.hd.tickets.set_priority(t.id, "urgent", self.cust.id)
        with self.assertRaises(PermissionDenied): self.hd.tickets.set_status(t.id, "closed", self.cust.id)
    def test_comments_and_internal_notes(self):
        t = self.ticket(); self.hd.tickets.add_comment(t.id, self.agent.id, "public"); self.hd.tickets.add_comment(t.id, self.agent.id, "secret", internal=True); t = self.hd.tickets.get(t.id)
        self.assertEqual([c.body for c in self.hd.tickets.visible_comments(t, self.cust)], ["public"]); self.assertEqual(len(self.hd.tickets.visible_comments(t, self.agent)), 2)
    def test_customers_cannot_write_internal_notes(self):
        with self.assertRaises(PermissionDenied): self.hd.tickets.add_comment(self.ticket().id, self.cust.id, "x", internal=True)
    def test_no_comments_on_closed_tickets(self):
        t = self.ticket(); self.hd.tickets.set_status(t.id, "closed", self.agent.id)
        with self.assertRaises(ValidationError): self.hd.tickets.add_comment(t.id, self.agent.id, "late")
    def test_customers_only_see_their_own_tickets(self):
        other = self.hd.users.create_user("Di", "customer"); mine = self.ticket(); self.ticket(requester=other)
        self.assertEqual([t.id for t in self.hd.tickets.list(self.cust.id)], [mine.id]); self.assertEqual(len(self.hd.tickets.list(self.agent.id)), 2)
        with self.assertRaises(NotFound): self.hd.tickets.get(2, self.cust.id)
    def test_list_filters(self):
        a = self.ticket(priority="high"); b = self.ticket("Other", priority="low", category="billing"); self.hd.tickets.assign(a.id, self.agent.id, self.admin.id)
        self.assertEqual([t.id for t in self.hd.tickets.list(self.admin.id, priority="low")], [b.id]); self.assertEqual([t.id for t in self.hd.tickets.list(self.admin.id, assignee_id=self.agent.id)], [a.id]); self.assertEqual([t.id for t in self.hd.tickets.list(self.admin.id, category="billing")], [b.id])
    def test_updated_changes_with_each_modification(self):
        t = self.ticket(); self.clock.ts = "2024-03-05T00:00:00"; self.hd.tickets.set_priority(t.id, "high", self.agent.id); self.assertEqual(self.hd.tickets.get(t.id).updated, "2024-03-05T00:00:00")
    def test_comment_notifications(self):
        t = self.ticket(); self.hd.tickets.assign(t.id, self.agent.id, self.admin.id); self.hd.tickets.add_comment(t.id, self.agent.id, "hello"); self.assertEqual([n["kind"] for n in self.hd.notifier.unread(self.cust.id)], ["comment"])
    def test_data_persists_between_instances(self): t = self.ticket(); self.hd.tickets.add_comment(t.id, self.agent.id, "x"); self.assertEqual(len(self.open().tickets.get(t.id).comments), 1)
    def test_unknown_ticket(self):
        with self.assertRaises(NotFound): self.hd.tickets.set_status(99, "closed", self.agent.id)
