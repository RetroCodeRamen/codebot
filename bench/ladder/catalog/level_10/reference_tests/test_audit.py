import os
from support import Base
from helpdesk.audit import AuditLog
from helpdesk.errors import NotFound, PermissionDenied, ValidationError

KEYS = ["seq", "ts", "actor", "action", "entity", "entity_id", "outcome", "changes", "reason"]

class TestAuditLog(Base):
    def last(self): return self.hd.audit.entries()[-1]
    def test_r01_hd_exposes_the_log_and_the_store_keeps_the_entries(self): self.assertIsInstance(self.hd.audit, AuditLog); self.assertIs(self.hd.store.audit, self.hd.store.audit); self.assertTrue(len(self.hd.store.audit) >= 3)
    def test_r01_entries_have_exactly_the_documented_keys(self):
        for e in self.hd.audit.entries(): self.assertEqual(sorted(e), sorted(KEYS))
    def test_r01_seq_counts_from_one_without_gaps(self): self.ticket(); self.assertEqual([e["seq"] for e in self.hd.audit.entries()], list(range(1, len(self.hd.audit.entries()) + 1)))
    def test_r01_record_appends_and_returns_the_entry(self):
        n = len(self.hd.audit.entries()); e = self.hd.audit.record(7, "custom.thing", "widget", 9, {"x": [1, 2]}); self.assertEqual((e["seq"], e["actor"], e["action"], e["entity"], e["entity_id"], e["outcome"], e["changes"], e["reason"], e["ts"]), (n + 1, 7, "custom.thing", "widget", 9, "ok", {"x": [1, 2]}, None, "2024-03-01T09:00:00"))
    def test_r01_record_defaults(self): e = self.hd.audit.record(None, "a.b", "e", None); self.assertEqual((e["changes"], e["outcome"], e["reason"]), ({}, "ok", None))
    def test_r01_timestamps_come_from_the_clock(self): self.clock.ts = "2024-05-05T05:05:05"; self.ticket(); self.assertEqual(self.last()["ts"], "2024-05-05T05:05:05")
    def test_r01_entries_returns_copies(self):
        self.ticket(); e = self.hd.audit.entries()[-1]; e["action"] = "tampered"; e["changes"]["x"] = 1; self.assertNotEqual(self.hd.audit.entries()[-1]["action"], "tampered"); self.assertNotIn("x", self.hd.audit.entries()[-1]["changes"])
    def test_r01_filters(self):
        t = self.ticket(); self.hd.tickets.assign(t.id, self.agent.id, self.admin.id); self.clock.ts = "2024-03-02T00:00:00"; self.hd.tickets.add_comment(t.id, self.agent.id, "x")
        A = self.hd.audit
        self.assertEqual({e["entity"] for e in A.entries(entity="user")}, {"user"}); self.assertEqual([e["action"] for e in A.entries(entity="ticket", entity_id=t.id)], ["ticket.create", "ticket.assign", "ticket.comment"])
        self.assertEqual([e["action"] for e in A.entries(actor=self.agent.id)], ["ticket.comment"]); self.assertEqual(len(A.entries(action="ticket.assign")), 1); self.assertEqual(A.entries(outcome="denied"), [])
        self.assertEqual([e["action"] for e in A.entries(since="2024-03-02T00:00:00")], ["ticket.comment"]); self.assertEqual(A.entries(actor=999), [])
    def test_r01_count_by_action_is_sorted(self):
        self.ticket(); self.ticket("B"); c = self.hd.audit.count_by_action(); self.assertEqual(list(c), sorted(c)); self.assertEqual((c["ticket.create"], c["user.create"]), (2, 3))
    def test_r01_the_log_is_stored_in_the_data_file(self):
        self.ticket(); doc = self.raw(); self.assertEqual([e["action"] for e in doc["audit"]][-1], "ticket.create"); self.assertEqual([e["seq"] for e in doc["audit"]], list(range(1, len(doc["audit"]) + 1)))
    def test_r01_the_log_survives_reopening_and_seq_continues(self):
        self.ticket(); n = len(self.hd.audit.entries()); hd2 = self.open(); self.assertEqual(len(hd2.audit.entries()), n); hd2.tickets.create(self.cust.id, "B", "d", actor_id=self.cust.id); self.assertEqual(hd2.audit.entries()[-1]["seq"], n + 1)
    def test_r01_an_entry_is_saved_together_with_its_change(self):
        t = self.ticket(); doc = self.raw(); self.assertTrue(any(x["id"] == t.id for x in doc["tickets"])); self.assertEqual(doc["audit"][-1]["entity_id"], t.id)
    def test_r01_a_failed_operation_leaves_no_entry_and_no_change(self):
        before = self.raw()
        with self.assertRaises(ValidationError): self.hd.tickets.create(self.cust.id, "  ", "d", actor_id=self.cust.id)
        self.assertEqual(self.raw(), before)

class TestAuditedOperations(Base):
    def last(self): return self.hd.audit.entries()[-1]
    def check(self, action, entity, entity_id, actor, changes):
        e = self.last(); self.assertEqual((e["action"], e["entity"], e["entity_id"], e["actor"], e["outcome"], e["reason"]), (action, entity, entity_id, actor, "ok", None)); self.assertEqual(e["changes"], changes)
    def test_r02_user_create_with_and_without_actor(self):
        self.check("user.create", "user", 3, None, {"name": [None, "Cy"], "role": [None, "customer"]}); u = self.hd.users.create_user("Di", "agent", actor_id=self.admin.id); self.check("user.create", "user", u.id, self.admin.id, {"name": [None, "Di"], "role": [None, "agent"]})
    def test_r02_user_create_with_an_actor_needs_manage_users(self):
        n = len(self.hd.audit.entries())
        with self.assertRaises(PermissionDenied): self.hd.users.create_user("X", "agent", actor_id=self.agent.id)
        e = self.last(); self.assertEqual((e["action"], e["outcome"], e["actor"], e["entity"], e["entity_id"], e["changes"]), ("user.create", "denied", self.agent.id, "user", None, {})); self.assertEqual(len(self.hd.audit.entries()), n + 1)
        with self.assertRaises(Exception): self.hd.users.find("X")
    def test_r02_user_disable_and_role(self):
        self.hd.users.disable(self.cust.id, self.admin.id); self.check("user.disable", "user", self.cust.id, self.admin.id, {"active": [True, False]}); self.hd.users.set_role(self.agent.id, "admin", self.admin.id); self.check("user.role", "user", self.agent.id, self.admin.id, {"role": [ "agent", "admin"]})
    def test_r02_ticket_create(self): t = self.ticket(priority="urgent"); self.check("ticket.create", "ticket", t.id, self.cust.id, {"title": [None, "Printer broken"], "severity": [None, "critical"], "category": [None, "general"], "requester_id": [None, self.cust.id]})
    def test_r02_ticket_create_by_an_agent_on_behalf_of_a_customer(self): t = self.hd.tickets.create(self.cust.id, "T", "d", actor_id=self.agent.id); self.assertEqual((self.last()["actor"], self.last()["changes"]["requester_id"]), (self.agent.id, [None, self.cust.id]))
    def test_r02_assign_includes_the_status_change_when_it_happens(self):
        t = self.ticket(); self.hd.tickets.assign(t.id, self.agent.id, self.admin.id); self.check("ticket.assign", "ticket", t.id, self.admin.id, {"assignee_id": [None, self.agent.id], "status": ["open", "in_progress"]})
        self.hd.tickets.set_status(t.id, "waiting", self.agent.id); self.hd.tickets.assign(t.id, self.admin.id, self.admin.id); self.check("ticket.assign", "ticket", t.id, self.admin.id, {"assignee_id": [self.agent.id, self.admin.id]})
    def test_r02_status_changes_and_resolution(self):
        t = self.ticket(); self.hd.tickets.set_status(t.id, "resolved", self.agent.id, "toner replaced"); self.check("ticket.status", "ticket", t.id, self.agent.id, {"status": ["open", "resolved"], "resolution": [None, "toner replaced"]})
        self.hd.tickets.set_status(t.id, "closed", self.agent.id); self.check("ticket.status", "ticket", t.id, self.agent.id, {"status": ["resolved", "closed"]}); self.hd.tickets.reopen(t.id, self.agent.id); self.check("ticket.status", "ticket", t.id, self.agent.id, {"status": ["closed", "open"], "resolution": ["toner replaced", None]})
    def test_r02_severity_changes_through_both_names(self):
        t = self.ticket(); self.hd.tickets.set_priority(t.id, "urgent", self.agent.id); self.check("ticket.severity", "ticket", t.id, self.agent.id, {"severity": ["medium", "critical"]}); self.hd.tickets.set_severity(t.id, "low", self.agent.id); self.check("ticket.severity", "ticket", t.id, self.agent.id, {"severity": ["critical", "low"]})
    def test_r02_comments(self):
        t = self.ticket(); c = self.hd.tickets.add_comment(t.id, self.agent.id, "secret", internal=True); self.check("ticket.comment", "ticket", t.id, self.agent.id, {"comment_id": [None, c.id], "internal": [None, True]})
    def test_r02_watch_unwatch_attach(self):
        t = self.ticket(); self.hd.tickets.watch(t.id, self.agent.id, self.admin.id); self.check("ticket.watch", "ticket", t.id, self.admin.id, {"watchers": [[], [self.agent.id]]}); self.hd.tickets.unwatch(t.id, self.agent.id, self.admin.id); self.check("ticket.unwatch", "ticket", t.id, self.admin.id, {"watchers": [[self.agent.id], []]})
        self.hd.tickets.add_attachment(t.id, "a.log", 5, "text/plain", self.cust.id); self.check("ticket.attach", "ticket", t.id, self.cust.id, {"attachments": [0, 1]})
    def test_r02_links(self):
        a, b = self.ticket("A"), self.ticket("B"); self.hd.links.link(a.id, "related", b.id, self.agent.id); self.check("ticket.link", "ticket", a.id, self.agent.id, {"links": [0, 1]}); self.hd.links.unlink(a.id, "related", b.id, self.agent.id); self.check("ticket.unlink", "ticket", a.id, self.agent.id, {"links": [1, 0]})
    def test_r02_purge_writes_one_entry_per_ticket(self):
        a, b = self.ticket("A"), self.ticket("B"); self.hd.tickets.set_status(a.id, "closed", self.agent.id); self.hd.tickets.set_status(b.id, "closed", self.agent.id); self.clock.ts = "2024-09-01T00:00:00"; n = len(self.hd.audit.entries())
        self.assertEqual(self.hd.retention.purge(30, self.admin.id), [a.id, b.id]); es = self.hd.audit.entries()[n:]; self.assertEqual([(e["action"], e["entity_id"], e["changes"], e["actor"]) for e in es], [("ticket.purge", a.id, {"status": ["closed", None]}, self.admin.id), ("ticket.purge", b.id, {"status": ["closed", None]}, self.admin.id)])
    def test_r02_bulk_escalation_and_rules_are_audited_through_the_services(self):
        t = self.ticket(priority="normal"); self.clock.ts = "2024-03-02T04:00:00"; self.hd.escalator.run(self.agent.id); self.check("ticket.severity", "ticket", t.id, self.agent.id, {"severity": ["medium", "high"]})
        a = self.ticket("W"); self.hd.tickets.set_status(a.id, "waiting", self.agent.id); self.clock.ts = "2024-06-01T00:00:00"; self.hd.bulk.close_stale(10, self.agent.id); changes = [e["changes"]["status"] for e in self.hd.audit.entries(entity="ticket", entity_id=a.id, action="ticket.status")]; self.assertEqual(changes, [["open", "waiting"], ["waiting", "resolved"], ["resolved", "closed"]]); self.assertEqual(self.hd.tickets.get(a.id).status, "closed")
    def test_r02_import_creates_audited_tickets(self):
        p = self.dir + "/in.csv"; open(p, "w").write("title,description\nA,first\nB,second\n"); n = len(self.hd.audit.entries()); created, errors = self.hd.importer.run(p, self.admin.id); self.assertEqual(len(self.hd.audit.entries()), n + 2); self.assertTrue(all(e["action"] == "ticket.create" and e["actor"] == self.admin.id for e in self.hd.audit.entries()[n:]))
    def test_r02_noops_create_no_entries(self):
        t = self.ticket(); self.hd.tickets.watch(t.id, self.agent.id, self.admin.id); self.hd.tickets.add_label(t.id, "bug", self.agent.id); self.hd.tickets.set_severity(t.id, "high", self.agent.id); self.hd.users.disable(self.cust.id, self.admin.id); n = len(self.hd.audit.entries())
        self.hd.tickets.watch(t.id, self.agent.id, self.admin.id); self.hd.tickets.add_label(t.id, "BUG", self.agent.id); self.hd.tickets.set_severity(t.id, "high", self.agent.id); self.hd.users.disable(self.cust.id, self.admin.id); self.assertEqual(len(self.hd.audit.entries()), n)
    def test_r02_denied_attempts(self):
        t = self.ticket(); cases = [(lambda: self.hd.tickets.assign(t.id, self.agent.id, self.cust.id), "ticket.assign", t.id), (lambda: self.hd.tickets.set_status(t.id, "closed", self.cust.id), "ticket.status", t.id), (lambda: self.hd.tickets.set_priority(t.id, "urgent", self.cust.id), "ticket.severity", t.id),
                                    (lambda: self.hd.tickets.add_comment(t.id, self.cust.id, "x", internal=True), "ticket.comment", t.id), (lambda: self.hd.users.disable(self.agent.id, self.cust.id), "user.disable", self.agent.id),
                                    (lambda: self.hd.retention.purge(1, self.agent.id), "ticket.purge", None), (lambda: self.hd.tickets.create(self.agent.id, "T", "d", actor_id=self.cust.id), "ticket.create", None)]
        for call, action, eid in cases:
            n = len(self.hd.audit.entries())
            with self.assertRaises(PermissionDenied): call()
            es = self.hd.audit.entries(); self.assertEqual(len(es), n + 1, action); e = es[-1]; self.assertEqual((e["action"], e["outcome"], e["entity_id"], e["changes"]), (action, "denied", eid, {})); self.assertTrue(e["reason"]); self.assertEqual(self.raw()["audit"][-1]["action"], action)
    def test_r02_a_denied_attempt_changes_nothing_else(self):
        t = self.ticket(); before = self.hd.store.tickets[t.id].to_dict()
        with self.assertRaises(PermissionDenied): self.hd.tickets.assign(t.id, self.agent.id, self.cust.id)
        self.assertEqual(self.hd.store.tickets[t.id].to_dict(), before)
    def test_r02_a_disabled_actor_is_denied_and_audited(self):
        t = self.ticket(); self.hd.users.disable(self.agent.id, self.admin.id)
        with self.assertRaises(PermissionDenied): self.hd.tickets.set_status(t.id, "closed", self.agent.id)
        self.assertEqual((self.last()["outcome"], self.last()["actor"], self.last()["action"]), ("denied", self.agent.id, "ticket.status"))
    def test_r02_validation_errors_and_unknown_ids_are_not_audited(self):
        t = self.ticket(); n = len(self.hd.audit.entries())
        for call, exc in ((lambda: self.hd.tickets.set_status(t.id, "resolved", self.agent.id), ValidationError), (lambda: self.hd.tickets.set_status(99, "closed", self.agent.id), NotFound), (lambda: self.hd.tickets.set_priority(t.id, "asap", self.agent.id), ValidationError), (lambda: self.hd.tickets.add_comment(t.id, self.agent.id, "  "), ValidationError)):
            with self.assertRaises(exc): call()
        self.assertEqual(len(self.hd.audit.entries()), n)
    def test_r02_notifications_are_not_audited(self): t = self.ticket(); self.hd.tickets.assign(t.id, self.agent.id, self.admin.id); self.assertFalse([e for e in self.hd.audit.entries() if e["action"].startswith("notification")])
