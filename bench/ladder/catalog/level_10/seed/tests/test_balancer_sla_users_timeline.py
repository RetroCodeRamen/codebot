from base import Base
from helpdesk import sla_report, timeline, user_csv
from helpdesk.errors import ValidationError

class TestBalancer(Base):
    def setUp(self): super().setUp(); self.eve = self.hd.users.create_user("Eve", "agent")
    def test_load_counts_open_tickets_per_agent(self):
        a = self.ticket("A"); b = self.ticket("B"); self.hd.tickets.assign(a.id, self.agent.id, self.admin.id); self.hd.tickets.assign(b.id, self.agent.id, self.admin.id); self.hd.tickets.set_status(b.id, "closed", self.admin.id)
        self.assertEqual(self.hd.balancer.load(), {self.admin.id: 0, self.agent.id: 1, self.eve.id: 0})
    def test_candidates_prefer_agents_over_admins_when_equal(self): self.assertEqual(self.hd.balancer.candidates(), [self.agent.id, self.eve.id, self.admin.id])
    def test_suggest_never_returns_the_current_assignee(self):
        t = self.ticket(); self.hd.tickets.assign(t.id, self.agent.id, self.admin.id); self.assertEqual(self.hd.balancer.suggest(t.id), self.eve.id)
    def test_autoassign(self): t = self.ticket(); self.assertEqual(self.hd.balancer.autoassign(t.id, self.admin.id).assignee_id, self.agent.id)
    def test_autoassign_without_candidates(self):
        t = self.ticket(); self.hd.users.disable(self.agent.id, self.admin.id); self.hd.users.disable(self.eve.id, self.admin.id); self.hd.tickets.assign(t.id, self.admin.id, self.admin.id)
        with self.assertRaises(ValidationError): self.hd.balancer.autoassign(t.id, self.admin.id)
    def test_rebalance_plan(self):
        ids = [self.ticket(f"T{i}").id for i in range(4)]
        for i in ids: self.hd.tickets.assign(i, self.agent.id, self.admin.id)
        plan = self.hd.balancer.rebalance_plan(); self.assertEqual(len(plan), 2); self.assertTrue(all(src == self.agent.id for _, src, _ in plan)); self.assertEqual({dst for _, _, dst in plan}, {self.eve.id, self.admin.id})
    def test_rebalance_of_an_even_load_is_empty(self): self.assertEqual(self.hd.balancer.rebalance_plan(), [])

class TestSlaReport(Base):
    def test_compliance_classification(self):
        a = self.ticket("A", priority="urgent"); b = self.ticket("B", priority="urgent"); c = self.ticket("C", priority="urgent"); d = self.ticket("D", priority="low")
        self.clock.ts = "2024-03-01T11:00:00"; self.hd.tickets.set_status(a.id, "closed", self.agent.id); self.clock.ts = "2024-03-01T15:00:00"; self.hd.tickets.set_status(b.id, "closed", self.agent.id)
        r = sla_report.compliance(self.hd.store, "2024-03-01T16:00:00"); self.assertEqual(r["urgent"], {"total": 3, "on_time": 1, "late": 1, "overdue": 1, "running": 0}); self.assertEqual(r["low"]["running"], 1)
        self.assertEqual(sla_report.compliance_rate(r["urgent"]), 0.5); self.assertIsNone(sla_report.compliance_rate(r["low"]))
    def test_worst_overdue_ordering(self): a = self.ticket("A", priority="urgent"); b = self.ticket("B", priority="high"); self.assertEqual(sla_report.worst_overdue(self.hd.store, "2024-03-01T20:00:00"), [(a.id, 7.0), (b.id, 3.0)]); self.assertEqual(len(sla_report.worst_overdue(self.hd.store, "2024-03-01T20:00:00", limit=1)), 1)

class TestUserCsv(Base):
    def test_export(self): text = user_csv.export_users(self.hd.store); self.assertEqual(text.splitlines()[0], "name,role,email,active"); self.assertIn("Bob,agent,,yes", text)
    def test_import_with_errors(self):
        created, errors = user_csv.import_users(self.hd.users, "name,role,email,active\nEve,agent,eve@x.org,yes\nFay,customer,,no\nAda,agent,,yes\nGus,boss,,yes\nHal,agent,,maybe\n")
        self.assertEqual(len(created), 2); self.assertEqual(len(errors), 3); self.assertFalse(self.hd.users.find("Fay").active); self.assertTrue(errors[0].startswith("row 4:"))
    def test_import_needs_a_name_column(self):
        with self.assertRaises(ValueError): user_csv.import_users(self.hd.users, "role\nagent\n")
    def test_round_trip_through_a_second_helpdesk(self):
        import os, tempfile; from helpdesk.app import Helpdesk
        other = Helpdesk(os.path.join(tempfile.mkdtemp(), "o.json"), self.clock); created, errors = user_csv.import_users(other.users, user_csv.export_users(self.hd.store)); self.assertEqual((len(created), errors), (3, []))

class TestTimeline(Base):
    def test_events_in_order_with_internal_notes_hidden(self):
        t = self.ticket(); self.clock.ts = "2024-03-01T10:00:00"; self.hd.tickets.add_comment(t.id, self.agent.id, "hi"); self.hd.tickets.add_comment(t.id, self.agent.id, "secret", internal=True)
        self.clock.ts = "2024-03-01T11:00:00"; self.hd.tickets.add_attachment(t.id, "log.txt", 10, "text/plain", self.cust.id); self.clock.ts = "2024-03-01T12:00:00"; self.hd.tickets.set_status(t.id, "closed", self.agent.id)
        text = timeline.render(self.hd.tickets.get(t.id), self.hd.store); self.assertEqual(len(text.splitlines()), 5); self.assertNotIn("secret", text); self.assertIn("secret", timeline.render(self.hd.tickets.get(t.id), self.hd.store, show_internal=True))
        self.assertEqual([e[0] for e in timeline.events(self.hd.tickets.get(t.id), self.hd.store)], sorted(e[0] for e in timeline.events(self.hd.tickets.get(t.id), self.hd.store)))
