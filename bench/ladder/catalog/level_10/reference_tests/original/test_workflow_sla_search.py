from base import Base
from helpdesk import sla, workflow
from helpdesk.errors import ValidationError
from helpdesk.search import parse_query, search

class TestWorkflowAndSla(Base):
    def test_allowed_transitions(self): self.assertEqual(workflow.allowed("resolved"), ["closed", "open"]); self.assertEqual(workflow.allowed("closed"), ["open"])
    def test_sla_hours_by_priority(self): self.assertEqual([sla.hours_for(p) for p in ("urgent", "high", "normal", "low")], [4, 8, 24, 72])
    def test_breached_only_when_not_resolved(self):
        t = self.ticket(priority="urgent"); self.assertFalse(sla.breached(t, "2024-03-01T12:59:59")); self.assertTrue(sla.breached(t, "2024-03-01T13:00:01"))
        self.hd.tickets.set_status(t.id, "resolved", self.agent.id, "ok"); self.assertFalse(sla.breached(t, "2024-03-09T00:00:00"))
    def test_remaining_hours(self): t = self.ticket(priority="high"); self.assertEqual(sla.remaining_hours(t, "2024-03-01T13:00:00"), 4.0)

class TestSearch(Base):
    def setUp(self):
        super().setUp(); self.a = self.ticket("Login fails on mobile", priority="high"); self.b = self.ticket("Invoice missing", category="billing"); self.hd.tickets.assign(self.a.id, self.agent.id, self.admin.id)
        self.hd.tickets.add_comment(self.b.id, self.agent.id, "checking the ledger"); self.hd.tickets.add_comment(self.b.id, self.agent.id, "secretword", internal=True)
    def all(self): return list(self.hd.store.tickets.values())
    def test_parse(self): q = parse_query("status:Open login error"); self.assertEqual((q.filters, q.terms), ({"status": "open"}, ["login", "error"]))
    def test_unknown_field(self):
        with self.assertRaises(ValidationError): parse_query("color:red")
    def test_empty_value(self):
        with self.assertRaises(ValidationError): parse_query("status:")
    def test_text_search_covers_title_description_and_public_comments(self):
        self.assertEqual([t.id for t in search(self.all(), "login")], [self.a.id]); self.assertEqual([t.id for t in search(self.all(), "ledger")], [self.b.id]); self.assertEqual(len(search(self.all(), "print")), 2)
    def test_internal_comments_are_not_searchable(self): self.assertEqual(search(self.all(), "secretword"), [])
    def test_filters(self):
        self.assertEqual([t.id for t in search(self.all(), "priority:high")], [self.a.id]); self.assertEqual([t.id for t in search(self.all(), "category:billing")], [self.b.id]); self.assertEqual([t.id for t in search(self.all(), f"assignee:{self.agent.id}")], [self.a.id])
    def test_all_terms_must_match(self): self.assertEqual(search(self.all(), "login invoice"), [])
    def test_newest_update_first(self): self.clock.ts = "2024-03-09T00:00:00"; self.hd.tickets.add_comment(self.a.id, self.agent.id, "later"); self.assertEqual([t.id for t in search(self.all(), "print")], [self.a.id, self.b.id])
    def test_limit(self): self.assertEqual(len(search(self.all(), "print", limit=1)), 1)
