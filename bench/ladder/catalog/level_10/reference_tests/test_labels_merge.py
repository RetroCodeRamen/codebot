from support import Base
from helpdesk import config, reports
from helpdesk.errors import NotFound, PermissionDenied, ValidationError
from helpdesk.search import parse_query, search

class TestLabels(Base):
    def setUp(self): super().setUp(); self.t = self.ticket()
    def add(self, label, actor=None): return self.hd.tickets.add_label(self.t.id, label, (actor or self.agent).id)
    def test_r05_add_labels_in_order(self): self.add("bug"); t = self.add("ui"); self.assertEqual(t.labels, ["bug", "ui"]); self.assertEqual(self.hd.tickets.get(self.t.id).labels, ["bug", "ui"])
    def test_r05_labels_are_lowercased_and_stripped(self): self.assertEqual(self.add("  Bug-Fix ").labels, ["bug-fix"])
    def test_r05_adding_an_existing_label_does_nothing(self): self.add("bug"); self.add("BUG"); self.assertEqual(self.hd.tickets.get(self.t.id).labels, ["bug"])
    def test_r05_invalid_labels(self):
        for bad in ("", "  ", "-x", "a b", "a_b", "x" * 25, "ä", "a/b", 5):
            with self.assertRaises(ValidationError, msg=repr(bad)): self.add(bad)
        self.assertEqual(self.hd.tickets.get(self.t.id).labels, [])
    def test_r05_label_length_boundary(self): self.assertEqual(self.add("x" * 24).labels, ["x" * 24]); self.assertEqual(self.add("0").labels[-1], "0")
    def test_r05_at_most_eight_labels(self):
        self.assertEqual(config.MAX_LABELS, 8)
        for i in range(8): self.add(f"l{i}")
        with self.assertRaises(ValidationError): self.add("one-too-many")
        self.assertEqual(self.add("l3").labels.count("l3"), 1)
    def test_r05_remove(self): self.add("a"); self.add("b"); t = self.hd.tickets.remove_label(self.t.id, "A", self.agent.id); self.assertEqual(t.labels, ["b"])
    def test_r05_removing_a_missing_label(self):
        with self.assertRaises(NotFound): self.hd.tickets.remove_label(self.t.id, "nope", self.agent.id)
    def test_r05_customers_cannot_label(self):
        with self.assertRaises(PermissionDenied): self.add("bug", self.cust)
        with self.assertRaises(PermissionDenied): self.hd.tickets.remove_label(self.t.id, "bug", self.cust.id)
        self.assertEqual(self.hd.audit.entries()[-1]["action"], "ticket.label_remove")
    def test_r05_admins_can_label(self): self.assertEqual(self.add("ops", self.admin).labels, ["ops"])
    def test_r05_unknown_ticket(self):
        with self.assertRaises(NotFound): self.hd.tickets.add_label(99, "x", self.agent.id)
    def test_r05_labels_touch_the_ticket_and_persist(self): self.clock.ts = "2024-03-09T00:00:00"; self.add("bug"); self.assertEqual(self.open().tickets.get(self.t.id).labels, ["bug"]); self.assertEqual(self.open().tickets.get(self.t.id).updated, "2024-03-09T00:00:00")
    def test_r05_label_permission_exists(self):
        from helpdesk import permissions
        self.assertIn("label", permissions.ACTIONS["admin"]); self.assertIn("label", permissions.ACTIONS["agent"]); self.assertNotIn("label", permissions.ACTIONS["customer"])
    def test_r05_search_by_label(self):
        other = self.ticket("Other"); self.add("bug"); self.hd.tickets.add_label(other.id, "bug", self.agent.id); self.hd.tickets.add_label(other.id, "ui", self.agent.id); ts = list(self.hd.store.tickets.values())
        self.assertEqual(sorted(t.id for t in search(ts, "label:bug")), [self.t.id, other.id]); self.assertEqual([t.id for t in search(ts, "label:bug label:ui")], [other.id]); self.assertEqual(search(ts, "label:nope"), []); self.assertEqual([t.id for t in search(ts, "label:UI")], [other.id])
    def test_r05_parse_query_collects_labels_in_a_list(self): self.assertEqual(parse_query("label:a label:B status:open").filters, {"label": ["a", "b"], "status": "open"}); self.assertEqual(parse_query("label:a").filters["label"], ["a"])
    def test_r05_empty_label_value_is_an_error(self):
        with self.assertRaises(ValidationError): parse_query("label:")
    def test_r05_list_by_label(self): other = self.ticket("Other"); self.add("bug"); self.assertEqual([t.id for t in self.hd.tickets.list(self.admin.id, label="bug")], [self.t.id]); self.assertEqual([t.id for t in self.hd.tickets.list(self.admin.id, label="BUG")], [self.t.id])
    def test_r05_summary_counts_tickets_per_label(self):
        other = self.ticket("Other"); self.add("bug"); self.hd.tickets.add_label(other.id, "bug", self.agent.id); self.add("ui"); self.assertEqual(reports.summary(self.hd.store, "2024-03-01T10:00:00")["by_label"], {"bug": 2, "ui": 1})

class TestMerge(Base):
    def setUp(self):
        super().setUp(); self.a = self.ticket("Original"); self.clock.ts = "2024-03-01T10:00:00"; self.b = self.ticket("Duplicate")
        self.clock.ts = "2024-03-01T11:00:00"; self.hd.tickets.add_comment(self.b.id, self.cust.id, "same problem here"); self.clock.ts = "2024-03-01T12:00:00"; self.hd.tickets.add_comment(self.a.id, self.agent.id, "on it")
        self.clock.ts = "2024-03-02T08:00:00"
    def merge(self, actor=None, src=None, dst=None): return self.hd.tickets.merge((src or self.b).id, (dst or self.a).id, (actor or self.agent).id)
    def test_r06_merge_returns_the_number_of_comments_moved(self): self.assertEqual(self.merge(), 1)
    def test_r06_source_is_closed_with_merge_data(self):
        self.merge(); s = self.hd.tickets.get(self.b.id); self.assertEqual((s.status, s.closed_at, s.resolution, s.merged_into, s.comments, s.updated), ("closed", "2024-03-02T08:00:00", f"merged into #{self.a.id}", self.a.id, [], "2024-03-02T08:00:00"))
    def test_r06_comments_move_keeping_ids_and_are_sorted(self):
        ids = {c.id: c for c in self.hd.tickets.get(self.b.id).comments}; self.merge(); t = self.hd.tickets.get(self.a.id); self.assertEqual([c.body for c in t.comments], ["same problem here", "on it"]); moved = t.comments[0]
        self.assertEqual((moved.ticket_id, moved.id in ids), (self.a.id, True)); self.assertEqual(moved.ts, "2024-03-01T11:00:00"); self.assertEqual(t.updated, "2024-03-02T08:00:00")
    def test_r06_labels_are_added_while_there_is_room(self):
        for l in ("a", "b", "c"): self.hd.tickets.add_label(self.b.id, l, self.agent.id)
        for l in ("c", "x", "y", "z", "w", "v"): self.hd.tickets.add_label(self.a.id, l, self.agent.id)
        self.merge(); self.assertEqual(self.hd.tickets.get(self.a.id).labels, ["c", "x", "y", "z", "w", "v", "a", "b"])
    def test_r06_labels_beyond_the_limit_are_dropped(self):
        for i in range(8): self.hd.tickets.add_label(self.a.id, f"k{i}", self.agent.id)
        self.hd.tickets.add_label(self.b.id, "extra", self.agent.id); self.merge(); self.assertEqual(len(self.hd.tickets.get(self.a.id).labels), 8); self.assertNotIn("extra", self.hd.tickets.get(self.a.id).labels)
    def test_r06_watchers_are_united(self):
        self.hd.tickets.watch(self.b.id, self.cust.id, self.cust.id); self.hd.tickets.watch(self.a.id, self.admin.id, self.admin.id); self.hd.tickets.watch(self.b.id, self.admin.id, self.admin.id); self.merge(); self.assertEqual(self.hd.tickets.get(self.a.id).watchers, [self.admin.id, self.cust.id])
    def test_r06_links_of_other_tickets_are_redirected_without_duplicates(self):
        c = self.ticket("Third"); self.hd.links.link(c.id, "related", self.b.id, self.agent.id); self.hd.links.link(c.id, "related", self.a.id, self.agent.id); self.hd.links.link(c.id, "blocks", self.b.id, self.agent.id); self.merge()
        self.assertEqual(self.hd.links.outgoing(c.id), [{"kind": "related", "target": self.a.id}, {"kind": "blocks", "target": self.a.id}])
    def test_r06_links_of_the_source_and_between_the_two_are_dropped(self):
        c = self.ticket("Third"); self.hd.links.link(self.b.id, "related", c.id, self.agent.id); self.hd.links.link(self.a.id, "related", self.b.id, self.agent.id); self.merge(); self.assertEqual(self.hd.links.outgoing(self.b.id), []); self.assertEqual(self.hd.links.outgoing(self.a.id), [])
    def test_r06_the_audit_entry(self):
        self.merge(); e = self.hd.audit.entries()[-1]; self.assertEqual((e["action"], e["entity"], e["entity_id"], e["actor"], e["outcome"], e["changes"]), ("ticket.merge", "ticket", self.b.id, self.agent.id, "ok", {"merged_into": [None, self.a.id], "status": ["open", "closed"], "comments_moved": [0, 1]}))
    def test_r06_exactly_one_entry_is_written(self): n = len(self.hd.audit.entries()); self.merge(); self.assertEqual(len(self.hd.audit.entries()), n + 1)
    def test_r06_the_audit_entry_records_the_previous_status(self): self.hd.tickets.assign(self.b.id, self.agent.id, self.admin.id); self.merge(src=self.b); self.assertEqual(self.hd.audit.entries()[-1]["changes"]["status"], ["in_progress", "closed"])
    def test_r06_the_source_assignee_is_notified_unless_it_is_the_actor(self):
        self.hd.tickets.assign(self.b.id, self.agent.id, self.admin.id); self.hd.notifier.mark_read(self.agent.id); self.hd.tickets.merge(self.b.id, self.a.id, self.admin.id); n = self.hd.notifier.unread(self.agent.id); self.assertEqual([(x["kind"], x["message"]) for x in n], [("status", f"ticket #{self.b.id} was merged into #{self.a.id}")])
    def test_r06_no_notification_when_the_assignee_merges(self):
        self.hd.tickets.assign(self.b.id, self.agent.id, self.admin.id); self.hd.notifier.mark_read(self.agent.id); self.merge(); self.assertEqual(self.hd.notifier.unread(self.agent.id), [])
    def test_r06_only_agents_and_admins_may_merge(self):
        with self.assertRaises(PermissionDenied): self.merge(actor=self.cust)
        self.assertEqual((self.hd.audit.entries()[-1]["action"], self.hd.audit.entries()[-1]["outcome"], self.hd.audit.entries()[-1]["entity_id"]), ("ticket.merge", "denied", self.b.id)); self.assertEqual(self.hd.tickets.get(self.b.id).status, "open")
        self.assertEqual(self.merge(actor=self.admin), 1)
    def test_r06_validation_errors_change_nothing(self):
        before = (self.hd.store.tickets[self.a.id].to_dict(), self.hd.store.tickets[self.b.id].to_dict(), len(self.hd.audit.entries()))
        with self.assertRaises(ValidationError): self.merge(src=self.a, dst=self.a)
        self.hd.tickets.set_status(self.a.id, "closed", self.agent.id); before = (self.hd.store.tickets[self.a.id].to_dict(), self.hd.store.tickets[self.b.id].to_dict(), len(self.hd.audit.entries()))
        with self.assertRaises(ValidationError): self.merge()                          # target is closed
        with self.assertRaises(ValidationError): self.merge(src=self.a, dst=self.b)    # source is closed
        self.assertEqual((self.hd.store.tickets[self.a.id].to_dict(), self.hd.store.tickets[self.b.id].to_dict(), len(self.hd.audit.entries())), before)
    def test_r06_a_merged_ticket_cannot_be_merged_again_or_be_a_target(self):
        c = self.ticket("Third"); self.merge()
        with self.assertRaises(ValidationError) as cm: self.hd.tickets.merge(self.b.id, c.id, self.agent.id)
        self.assertIn(f"merged into #{self.a.id}", str(cm.exception))
        with self.assertRaises(ValidationError) as cm2: self.hd.tickets.merge(c.id, self.b.id, self.agent.id)
        self.assertIn(f"#{self.a.id}", str(cm2.exception))
    def test_r06_unknown_tickets(self):
        with self.assertRaises(NotFound): self.hd.tickets.merge(99, self.a.id, self.agent.id)
        with self.assertRaises(NotFound): self.hd.tickets.merge(self.b.id, 99, self.agent.id)
    def test_r06_merge_persists(self): self.merge(); hd2 = self.open(); self.assertEqual(hd2.tickets.get(self.b.id).merged_into, self.a.id); self.assertEqual(len(hd2.tickets.get(self.a.id).comments), 2)
    def test_r06_the_merged_data_is_stored_in_the_file(self): self.merge(); d = {t["id"]: t for t in self.raw()["tickets"]}; self.assertEqual((d[self.b.id]["merged_into"], d[self.b.id]["comments"], d[self.a.id]["merged_into"]), (self.a.id, [], None))
    def test_r06_customers_cannot_see_internal_comments_after_a_merge(self):
        self.hd.tickets.add_comment(self.b.id, self.agent.id, "internal info", internal=True); self.merge(); t = self.hd.tickets.get(self.a.id); self.assertEqual(len(self.hd.tickets.visible_comments(t, self.cust)), 2); self.assertEqual(len(self.hd.tickets.visible_comments(t, self.agent)), 3)
    def test_r06_merging_a_ticket_without_comments(self): c = self.ticket("Empty"); self.assertEqual(self.hd.tickets.merge(c.id, self.a.id, self.agent.id), 0)
    def test_r06_merge_permission_exists(self):
        from helpdesk import permissions
        self.assertTrue(all("merge" in permissions.ACTIONS[r] for r in ("admin", "agent"))); self.assertNotIn("merge", permissions.ACTIONS["customer"])
