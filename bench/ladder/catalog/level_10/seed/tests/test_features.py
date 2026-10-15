import json, os
from base import Base
from helpdesk import escalation, kb, report_formats, settings, templates
from helpdesk.digest import build_digest
from helpdesk.errors import NotFound, PermissionDenied, ValidationError
from helpdesk.stats import agent_performance, first_response_hours, percentile, summary_percentiles

class TestEscalation(Base):
    def test_next_priority(self): self.assertEqual([escalation.next_priority(p) for p in ("low", "normal", "high", "urgent")], ["normal", "high", "urgent", None])
    def test_candidates_use_the_sla_fraction(self):
        t = self.ticket(priority="normal"); self.assertEqual(escalation.candidates(self.hd.store, "2024-03-01T20:00:00"), []); self.assertEqual([x.id for x in escalation.candidates(self.hd.store, "2024-03-02T03:00:00")], [t.id])
    def test_urgent_and_finished_tickets_are_left_alone(self):
        u = self.ticket(priority="urgent"); r = self.ticket("R"); self.hd.tickets.set_status(r.id, "closed", self.agent.id); self.assertEqual(escalation.candidates(self.hd.store, "2024-03-09T00:00:00"), [])
    def test_run_raises_priority_and_recomputes_the_deadline(self):
        t = self.ticket(priority="normal"); self.clock.ts = "2024-03-02T04:00:00"; self.assertEqual(self.hd.escalator.run(self.agent.id), [(t.id, "normal", "high")]); self.assertEqual(self.hd.tickets.get(t.id).sla_due, "2024-03-01T17:00:00")
    def test_preview_changes_nothing(self): t = self.ticket(priority="low"); self.clock.ts = "2024-03-09T00:00:00"; self.assertEqual(self.hd.escalator.preview(), [(t.id, "low", "normal")]); self.assertEqual(self.hd.tickets.get(t.id).priority, "low")
    def test_customers_cannot_escalate(self):
        self.ticket(); self.clock.ts = "2024-03-09T00:00:00"
        with self.assertRaises(PermissionDenied): self.hd.escalator.run(self.cust.id)

class TestBulk(Base):
    def test_close_stale_closes_old_waiting_and_resolved_tickets(self):
        a = self.ticket("A"); b = self.ticket("B"); c = self.ticket("C"); self.hd.tickets.set_status(a.id, "waiting", self.agent.id); self.hd.tickets.set_status(b.id, "resolved", self.agent.id, "done")
        self.clock.ts = "2024-03-20T09:00:00"; self.assertEqual(self.hd.bulk.close_stale(10, self.agent.id), [a.id, b.id]); self.assertEqual(self.hd.tickets.get(c.id).status, "open"); self.assertEqual(self.hd.tickets.get(a.id).status, "closed")
    def test_recent_tickets_are_not_stale(self): a = self.ticket(); self.hd.tickets.set_status(a.id, "waiting", self.agent.id); self.clock.ts = "2024-03-03T09:00:00"; self.assertEqual(self.hd.bulk.stale(5), [])
    def test_reassign(self):
        other = self.hd.users.create_user("Eve", "agent"); a = self.ticket("A"); b = self.ticket("B"); self.hd.tickets.assign(a.id, self.agent.id, self.admin.id); self.hd.tickets.assign(b.id, self.agent.id, self.admin.id); self.hd.tickets.set_status(b.id, "closed", self.admin.id)
        self.assertEqual(self.hd.bulk.reassign(self.agent.id, other.id, self.admin.id), [a.id]); self.assertEqual(self.hd.tickets.get(a.id).assignee_id, other.id); self.assertEqual(self.hd.tickets.get(b.id).assignee_id, self.agent.id)
    def test_bulk_priority_skips_unchanged(self): a = self.ticket(priority="high"); b = self.ticket("B"); self.assertEqual(self.hd.bulk.set_priority([a.id, b.id], "high", self.agent.id), [b.id])

class TestTemplatesImportStatsDigest(Base):
    def test_template_rendering(self):
        t = self.ticket(); self.hd.tickets.assign(t.id, self.agent.id, self.admin.id); text = templates.render("received", self.hd.tickets.get(t.id), self.hd.store, self.agent); self.assertIn("Hello Cy", text); self.assertIn("#1", text); self.assertIn("-- Bob", text)
    def test_unknown_template(self):
        with self.assertRaises(NotFound): templates.render("nope", self.ticket(), self.hd.store, self.agent)
    def test_reply_posts_a_comment(self): t = self.ticket(); c = self.hd.replier.reply(t.id, "need-info", self.agent.id); self.assertEqual(c.author_id, self.agent.id); self.assertIn("more information", self.hd.tickets.get(t.id).comments[0].body)
    def test_import_creates_tickets_and_reports_bad_rows(self):
        p = self.dir + "/in.csv"; open(p, "w").write("title,description,priority,category,requester\nA,first,high,billing,Cy\nB,second,,,\n,missing title,low,general,Cy\nC,third,bogus,general,Cy\nD,fourth,low,general,nobody\n")
        created, errors = self.hd.importer.run(p, self.admin.id); self.assertEqual(created, [1, 2]); self.assertEqual(len(errors), 3); self.assertTrue(errors[0].startswith("row 4:")); self.assertEqual(self.hd.tickets.get(2).requester_id, self.admin.id)
    def test_import_needs_the_title_and_description_columns(self):
        p = self.dir + "/bad.csv"; open(p, "w").write("title\nA\n")
        with self.assertRaises(ValueError): self.hd.importer.run(p, self.admin.id)
    def test_percentile(self): self.assertEqual(percentile([1, 2, 3, 4], 50), 2.5); self.assertEqual(percentile([5], 90), 5.0); self.assertIsNone(percentile([], 50)); self.assertEqual(percentile([1, 2, 3, 4, 5], 100), 5.0)
    def test_first_response_times(self):
        t = self.ticket(); self.clock.ts = "2024-03-01T11:30:00"; self.hd.tickets.add_comment(t.id, self.cust.id, "me again"); self.hd.tickets.add_comment(t.id, self.agent.id, "secret", internal=True); self.assertEqual(first_response_hours(self.hd.store), {})
        self.clock.ts = "2024-03-01T12:00:00"; self.hd.tickets.add_comment(t.id, self.agent.id, "hi"); self.assertEqual(first_response_hours(self.hd.store), {t.id: 3.0}); self.assertEqual(summary_percentiles(self.hd.store)["p50"], 3.0)
    def test_agent_performance(self):
        a = self.ticket("A"); self.hd.tickets.assign(a.id, self.agent.id, self.admin.id); self.clock.ts = "2024-03-01T15:00:00"; self.hd.tickets.set_status(a.id, "closed", self.agent.id); self.assertEqual(agent_performance(self.hd.store), {self.agent.id: {"assigned": 1, "closed": 1, "avg_close_hours": 6.0}})
    def test_digest(self):
        t = self.ticket(); self.hd.tickets.assign(t.id, self.agent.id, self.admin.id); text = build_digest(self.hd.store, self.hd.notifier, self.agent.id); self.assertIn("1 new notification(s) on 1 ticket(s)", text); self.assertIn("assigned to you", text)
        self.hd.notifier.mark_read(self.agent.id); self.assertIn("nothing new", build_digest(self.hd.store, self.hd.notifier, self.agent.id))

class TestWatchAttachRetentionKbSettings(Base):
    def test_watchers_are_notified_of_comments_and_status_changes(self):
        t = self.ticket(); w = self.hd.users.create_user("Eve", "agent"); self.hd.tickets.watch(t.id, w.id, self.admin.id); self.hd.tickets.add_comment(t.id, self.agent.id, "x"); self.hd.tickets.set_status(t.id, "resolved", self.agent.id, "fixed")
        self.assertEqual(sorted(n["kind"] for n in self.hd.notifier.unread(w.id)), ["comment", "status"])
    def test_customers_can_only_watch_for_themselves(self):
        t = self.ticket()
        with self.assertRaises(PermissionDenied): self.hd.tickets.watch(t.id, self.agent.id, self.cust.id)
        self.hd.tickets.watch(t.id, self.cust.id, self.cust.id); self.hd.tickets.watch(t.id, self.cust.id, self.cust.id); self.assertEqual(self.hd.tickets.get(t.id).watchers, [self.cust.id]); self.hd.tickets.unwatch(t.id, self.cust.id, self.cust.id); self.assertEqual(self.hd.tickets.get(t.id).watchers, [])
    def test_attachments_are_validated(self):
        t = self.ticket(); a = self.hd.tickets.add_attachment(t.id, "log.txt", 120, "text/plain", self.cust.id); self.assertEqual((a["name"], a["size"], a["added_by"]), ("log.txt", 120, self.cust.id))
        for args in (("a/b.txt", 1, "x/y"), ("ok", 0, "x/y"), ("ok", 11 * 1024 * 1024, "x/y"), ("", 5, "x/y"), ("ok", 5, "")):
            with self.assertRaises(ValidationError): self.hd.tickets.add_attachment(t.id, *args, self.cust.id)
    def test_at_most_ten_attachments(self):
        t = self.ticket()
        for i in range(10): self.hd.tickets.add_attachment(t.id, f"f{i}", 1, "a/b", self.cust.id)
        with self.assertRaises(ValidationError): self.hd.tickets.add_attachment(t.id, "one-too-many", 1, "a/b", self.cust.id)
    def test_purge_archives_then_removes_old_closed_tickets(self):
        a = self.ticket("A"); b = self.ticket("B"); self.hd.tickets.set_status(a.id, "closed", self.agent.id); self.clock.ts = "2024-06-01T00:00:00"; self.hd.tickets.set_status(b.id, "closed", self.agent.id)
        self.assertEqual(self.hd.retention.purge(30, self.admin.id), [a.id]); self.assertEqual(sorted(self.hd.store.tickets), [b.id]); self.assertEqual([t["id"] for t in self.hd.retention.archived()], [a.id]); self.assertEqual(sorted(self.open().store.tickets), [b.id])
    def test_purge_is_admin_only(self):
        with self.assertRaises(PermissionDenied): self.hd.retention.purge(1, self.agent.id)
    def test_knowledge_base_suggestions(self):
        p = self.dir + "/hd.json.kb.json"; json.dump([{"title": "Printer troubleshooting", "body": "toner paper jam"}, {"title": "Invoice questions", "body": "billing"}], open(p, "w")); k = kb.KnowledgeBase(p); t = self.ticket("Printer has a paper jam")
        self.assertEqual([a["title"] for a in k.suggest(t)], ["Printer troubleshooting"])
    def test_settings_override_sla_hours(self):
        from helpdesk import config
        old = dict(config.SLA_HOURS)
        try: self.assertEqual(settings.apply({"sla_hours": {"urgent": 2}}), ["sla_hours"]); self.assertEqual(config.SLA_HOURS["urgent"], 2)
        finally: config.SLA_HOURS.update(old)
    def test_settings_validation(self):
        with self.assertRaises(ValidationError): settings.apply({"sla_hours": {"bogus": 2}})
        with self.assertRaises(ValidationError): settings.apply({"sla_hours": {"urgent": -1}})
    def test_summary_formats(self):
        self.ticket(); r = __import__("helpdesk.reports", fromlist=["summary"]).summary(self.hd.store, "2024-03-01T10:00:00"); self.assertIn("Total: 1", report_formats.render_summary(r)); self.assertIn("# Helpdesk summary", report_formats.render_summary(r, "markdown")); self.assertEqual(json.loads(report_formats.render_summary(r, "json"))["total"], 1)
