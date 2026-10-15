from base import Base
from helpdesk import sla_calendar as cal
from helpdesk.errors import NotFound, PermissionDenied, ValidationError

class TestLinks(Base):
    def setUp(self): super().setUp(); self.a = self.ticket("A"); self.b = self.ticket("B"); self.c = self.ticket("C")
    def test_link_and_describe(self):
        self.hd.links.link(self.a.id, "blocks", self.b.id, self.agent.id); self.assertEqual(self.hd.links.outgoing(self.a.id), [{"kind": "blocks", "target": self.b.id}]); self.assertEqual(self.hd.links.incoming(self.b.id), [(self.a.id, "blocks")])
        self.assertEqual(self.hd.links.describe(self.a.id), [f"blocks #{self.b.id}"]); self.assertEqual(self.hd.links.describe(self.b.id), [f"blocked by #{self.a.id}"])
    def test_linking_twice_is_harmless(self): self.hd.links.link(1, "related", 2, self.agent.id); self.hd.links.link(1, "related", 2, self.agent.id); self.assertEqual(len(self.hd.links.outgoing(1)), 1)
    def test_validation(self):
        with self.assertRaises(ValidationError): self.hd.links.link(1, "weird", 2, self.agent.id)
        with self.assertRaises(ValidationError): self.hd.links.link(1, "related", 1, self.agent.id)
        with self.assertRaises(NotFound): self.hd.links.link(1, "related", 99, self.agent.id)
    def test_customers_cannot_link(self):
        with self.assertRaises(PermissionDenied): self.hd.links.link(1, "related", 2, self.cust.id)
    def test_unlink(self):
        self.hd.links.link(1, "related", 2, self.agent.id); self.hd.links.unlink(1, "related", 2, self.agent.id); self.assertEqual(self.hd.links.outgoing(1), [])
        with self.assertRaises(NotFound): self.hd.links.unlink(1, "related", 2, self.agent.id)
    def test_blockers_ignore_closed_tickets(self):
        self.hd.links.link(1, "blocks", 3, self.agent.id); self.hd.links.link(2, "blocks", 3, self.agent.id); self.hd.tickets.set_status(1, "closed", self.agent.id); self.assertEqual(self.hd.links.blockers(3), [2])
    def test_links_persist(self): self.hd.links.link(1, "duplicates", 2, self.agent.id); self.assertEqual(self.open().links.outgoing(1), [{"kind": "duplicates", "target": 2}])

class TestSavedSearches(Base):
    def test_save_list_run_delete(self):
        t = self.ticket("Login fails", priority="high"); self.ticket("Invoice"); self.hd.saved.save(self.agent.id, "urgent-ish", "priority:high")
        self.assertEqual([s["name"] for s in self.hd.saved.list(self.agent.id)], ["urgent-ish"]); self.assertEqual([x.id for x in self.hd.saved.run(self.agent.id, "urgent-ish")], [t.id])
        self.hd.saved.delete(self.agent.id, "urgent-ish"); self.assertEqual(self.hd.saved.list(self.agent.id), [])
    def test_saving_again_replaces_the_query(self): self.hd.saved.save(self.agent.id, "q", "status:open"); self.hd.saved.save(self.agent.id, "q", "status:closed"); self.assertEqual([s["query"] for s in self.hd.saved.list(self.agent.id)], ["status:closed"])
    def test_bad_names_and_queries(self):
        for name in ("", "UPPER", "has space", "x" * 31):
            with self.assertRaises(ValidationError): self.hd.saved.save(self.agent.id, name, "status:open")
        with self.assertRaises(ValidationError): self.hd.saved.save(self.agent.id, "ok", "color:red")
    def test_searches_are_per_user(self): self.hd.saved.save(self.agent.id, "mine", "status:open"); self.assertEqual(self.hd.saved.list(self.admin.id), []); 
    def test_unknown_search(self):
        with self.assertRaises(NotFound): self.hd.saved.run(self.agent.id, "nope")
    def test_customers_run_searches_over_their_own_tickets_only(self):
        other = self.hd.users.create_user("Di", "customer"); self.ticket("Mine"); self.ticket("Theirs", requester=other); self.hd.saved.save(self.cust.id, "all", "status:open"); self.assertEqual([t.title for t in self.hd.saved.run(self.cust.id, "all")], ["Mine"])
    def test_saved_searches_persist(self): self.hd.saved.save(self.agent.id, "q", "status:open"); self.assertEqual(len(self.open().saved.list(self.agent.id)), 1)

class TestCalendar(Base):
    def test_add_business_hours_within_a_day(self): self.assertEqual(cal.add_business_hours("2024-03-04T10:00:00", 3), "2024-03-04T13:00:00")
    def test_overflowing_into_the_next_day(self): self.assertEqual(cal.add_business_hours("2024-03-04T15:00:00", 4), "2024-03-05T11:00:00")
    def test_weekends_are_skipped(self): self.assertEqual(cal.add_business_hours("2024-03-08T16:00:00", 2), "2024-03-11T10:00:00")
    def test_starting_outside_business_hours(self): self.assertEqual(cal.add_business_hours("2024-03-02T12:00:00", 1), "2024-03-04T10:00:00"); self.assertEqual(cal.add_business_hours("2024-03-04T07:00:00", 1), "2024-03-04T10:00:00")
    def test_business_hours_between(self):
        self.assertEqual(cal.business_hours_between("2024-03-04T10:00:00", "2024-03-04T12:30:00"), 2.5); self.assertEqual(cal.business_hours_between("2024-03-08T16:00:00", "2024-03-11T10:00:00"), 2.0)
        self.assertEqual(cal.business_hours_between("2024-03-04T10:00:00", "2024-03-04T09:00:00"), 0.0); self.assertEqual(cal.business_hours_between("2024-03-02T10:00:00", "2024-03-03T10:00:00"), 0.0)
    def test_round_trip(self): s = "2024-03-05T09:30:00"; e = cal.add_business_hours(s, 11); self.assertEqual(cal.business_hours_between(s, e), 11.0)
