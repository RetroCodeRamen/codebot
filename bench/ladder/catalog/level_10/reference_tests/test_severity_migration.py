import csv, json, os
from support import Base, Clock, v1_document
from helpdesk import config, export, reports, sla, store as store_mod
from helpdesk.app import Helpdesk
from helpdesk.errors import StoreError, ValidationError
from helpdesk.models import Ticket
from helpdesk.search import parse_query, search

class TestSeverity(Base):
    def test_r03_config_constants(self):
        self.assertEqual(config.SEVERITIES, ("low", "medium", "high", "critical")); self.assertEqual(config.PRIORITIES, ("low", "normal", "high", "urgent"))
        self.assertEqual(config.PRIORITY_TO_SEVERITY, {"low": "low", "normal": "medium", "high": "high", "urgent": "critical"}); self.assertEqual(config.SEVERITY_TO_PRIORITY, {"low": "low", "medium": "normal", "high": "high", "critical": "urgent"}); self.assertEqual(config.SLA_HOURS, {"urgent": 4, "high": 8, "normal": 24, "low": 72})
    def test_r03_ticket_property_maps_both_ways(self):
        t = Ticket(1, "T", "d", 3); self.assertEqual((t.severity, t.priority), ("medium", "normal")); t.priority = "urgent"; self.assertEqual((t.severity, t.priority), ("critical", "urgent")); t.severity = "low"; self.assertEqual(t.priority, "low")
    def test_r03_ticket_constructor_accepts_both_vocabularies(self):
        self.assertEqual(Ticket(1, "T", "d", 3, None, "open", "urgent").severity, "critical"); self.assertEqual(Ticket(1, "T", "d", 3, severity="high").priority, "high"); self.assertEqual(Ticket(1, "T", "d", 3, priority="high", severity="high").severity, "high")
        with self.assertRaises(ValueError): Ticket(1, "T", "d", 3, priority="high", severity="low")
        with self.assertRaises(ValueError): Ticket(1, "T", "d", 3, priority="asap")
        with self.assertRaises(ValueError): Ticket(1, "T", "d", 3, severity="asap")
    def test_r03_ticket_has_the_new_fields_with_defaults(self): t = Ticket(1, "T", "d", 3); self.assertEqual((t.labels, t.merged_into, t.watchers, t.attachments, t.links), ([], None, [], [], []))
    def test_r03_to_dict_is_the_stored_form(self):
        d = Ticket(1, "T", "d", 3, severity="high", labels=["a"]).to_dict(); self.assertEqual(d["severity"], "high"); self.assertNotIn("priority", d); self.assertEqual((d["labels"], d["merged_into"]), (["a"], None))
    def test_r03_from_dict_accepts_both_forms(self):
        old = {"id": 1, "title": "T", "description": "d", "requester_id": 3, "priority": "urgent"}; new = {**old, "severity": "high"}; del new["priority"]
        self.assertEqual(Ticket.from_dict(old).severity, "critical"); self.assertEqual(Ticket.from_dict(new).severity, "high"); self.assertEqual(Ticket.from_dict(Ticket.from_dict(new).to_dict()), Ticket.from_dict(new))
    def test_r03_create_with_old_priority(self):
        t = self.ticket(priority="urgent"); self.assertEqual((t.severity, t.priority, t.sla_due), ("critical", "urgent", "2024-03-01T13:00:00"))
    def test_r03_create_with_severity(self): t = self.hd.tickets.create(self.cust.id, "T", "d", actor_id=self.cust.id, severity="high"); self.assertEqual((t.severity, t.priority, t.sla_due), ("high", "high", "2024-03-01T17:00:00"))
    def test_r03_create_defaults_to_medium(self): t = self.ticket(); self.assertEqual((t.severity, t.priority), ("medium", "normal"))
    def test_r03_priority_and_severity_that_disagree(self):
        with self.assertRaises(ValidationError): self.hd.tickets.create(self.cust.id, "T", "d", priority="urgent", severity="low", actor_id=self.cust.id)
        t = self.hd.tickets.create(self.cust.id, "T", "d", priority="urgent", severity="critical", actor_id=self.cust.id); self.assertEqual(t.severity, "critical")
    def test_r03_invalid_values(self):
        with self.assertRaises(ValidationError): self.hd.tickets.create(self.cust.id, "T", "d", priority="asap", actor_id=self.cust.id)
        with self.assertRaises(ValidationError): self.hd.tickets.create(self.cust.id, "T", "d", severity="urgent", actor_id=self.cust.id)
    def test_r03_set_severity_and_set_priority_recompute_the_deadline(self):
        t = self.ticket(); self.hd.tickets.set_severity(t.id, "critical", self.agent.id); self.assertEqual(self.hd.tickets.get(t.id).sla_due, "2024-03-01T13:00:00"); self.hd.tickets.set_priority(t.id, "low", self.agent.id); self.assertEqual((self.hd.tickets.get(t.id).severity, self.hd.tickets.get(t.id).sla_due), ("low", "2024-03-04T09:00:00"))
    def test_r03_set_severity_validates_and_checks_permissions(self):
        t = self.ticket()
        with self.assertRaises(ValidationError): self.hd.tickets.set_severity(t.id, "normal", self.agent.id)
        from helpdesk.errors import PermissionDenied
        with self.assertRaises(PermissionDenied): self.hd.tickets.set_severity(t.id, "high", self.cust.id)
    def test_r03_list_filters(self):
        a = self.ticket("A", priority="high"); b = self.ticket("B", priority="low"); c = self.ticket("C"); L = self.hd.tickets.list
        self.assertEqual([t.id for t in L(self.admin.id, priority="low")], [b.id]); self.assertEqual([t.id for t in L(self.admin.id, severity="low")], [b.id]); self.assertEqual([t.id for t in L(self.admin.id, severity="medium")], [c.id]); self.assertEqual([t.id for t in L(self.admin.id, priority="normal")], [c.id])
    def test_r03_sla_helpers(self):
        self.assertEqual((sla.hours_for_severity("critical"), sla.hours_for_severity("medium")), (4, 24)); self.assertEqual(sla.due_for_severity("high", "2024-03-01T09:00:00"), "2024-03-01T17:00:00"); self.assertEqual(sla.due_for("urgent", "2024-03-01T09:00:00"), "2024-03-01T13:00:00")
    def test_r03_settings_override_affects_both_vocabularies(self):
        from helpdesk import settings
        old = dict(config.SLA_HOURS)
        try: settings.apply({"sla_hours": {"urgent": 2}}); self.assertEqual(sla.hours_for_severity("critical"), 2); self.assertEqual(sla.due_for_severity("critical", "2024-03-01T09:00:00"), "2024-03-01T11:00:00")
        finally: config.SLA_HOURS.update(old)
    def test_r03_search_filters_in_both_vocabularies(self):
        a = self.ticket("Alpha", priority="urgent"); b = self.ticket("Beta"); ts = list(self.hd.store.tickets.values())
        self.assertEqual([t.id for t in search(ts, "severity:critical")], [a.id]); self.assertEqual([t.id for t in search(ts, "priority:urgent")], [a.id]); self.assertEqual([t.id for t in search(ts, "severity:medium")], [b.id]); self.assertEqual([t.id for t in search(ts, "priority:normal")], [b.id]); self.assertEqual(search(ts, "severity:urgent"), [])
    def test_r03_parse_query_keeps_old_behaviour_for_single_valued_fields(self): q = parse_query("status:Open priority:High login"); self.assertEqual((q.filters, q.terms), ({"status": "open", "priority": "high"}, ["login"]))
    def test_r03_summary_has_both_breakdowns(self):
        self.ticket(priority="urgent"); self.ticket(); r = reports.summary(self.hd.store, "2024-03-01T10:00:00"); self.assertEqual(r["by_priority"], {"urgent": 1, "normal": 1}); self.assertEqual(r["by_severity"], {"critical": 1, "medium": 1}); self.assertEqual(r["by_label"], {})
    def test_r03_escalation_reports_old_names(self): t = self.ticket(priority="normal"); self.clock.ts = "2024-03-02T04:00:00"; self.assertEqual(self.hd.escalator.run(self.agent.id), [(t.id, "normal", "high")]); self.assertEqual(self.hd.tickets.get(t.id).severity, "high")
    def test_r03_csv_export_appends_columns(self):
        t = self.ticket(priority="urgent"); self.hd.tickets.add_label(t.id, "bug", self.agent.id); self.hd.tickets.add_label(t.id, "ui", self.agent.id); p = self.dir + "/t.csv"; export.export_csv(self.hd.store, p); rows = list(csv.reader(open(p)))
        self.assertEqual(rows[0], ["id", "title", "status", "priority", "category", "requester", "assignee", "created", "updated", "sla_due", "severity", "labels", "merged_into"]); self.assertEqual(rows[1][3], "urgent"); self.assertEqual(rows[1][10:], ["critical", "bug|ui", ""])
    def test_r03_json_export_keeps_priority_and_adds_the_new_keys(self):
        t = self.ticket(priority="urgent"); p = self.dir + "/t.json"; export.export_json(self.hd.store, p); d = json.load(open(p))["tickets"][0]; self.assertEqual((d["priority"], d["severity"], d["labels"], d["merged_into"]), ("urgent", "critical", [], None))
    def test_r03_stored_tickets_have_severity_and_no_priority(self): self.ticket(priority="high"); d = self.raw()["tickets"][0]; self.assertEqual(d["severity"], "high"); self.assertNotIn("priority", d)

class TestMigration(Base):
    def setUp(self): super().setUp(); self.p1 = os.path.join(self.dir, "old.json"); self.write_v1(self.p1)
    def open_old(self): return Helpdesk(self.p1, self.clock)
    def test_r04_version_constant(self): self.assertEqual(store_mod.VERSION, 2); self.assertEqual(self.raw()["version"], 2)
    def test_r04_a_v1_file_is_migrated_on_open_and_rewritten_at_once(self):
        hd = self.open_old(); doc = self.raw(self.p1); self.assertEqual(doc["version"], 2); self.assertEqual([t["severity"] for t in doc["tickets"]], ["low", "medium", "high", "critical", "medium"]); self.assertTrue(all("priority" not in t for t in doc["tickets"]))
        self.assertTrue(all(t["labels"] == [] and t["merged_into"] is None for t in doc["tickets"])); self.assertEqual(len(hd.store.tickets), 5)
    def test_r04_the_original_is_kept_as_a_backup(self): original = open(self.p1).read(); self.open_old(); self.assertEqual(open(self.p1 + ".v1.bak").read(), original)
    def test_r04_the_migration_is_the_first_audit_entry(self):
        self.open_old(); e = self.raw(self.p1)["audit"]; self.assertEqual(len(e), 1); self.assertEqual(e[0], {"seq": 1, "ts": "2024-03-01T09:00:00", "actor": None, "action": "store.migrate", "entity": "store", "entity_id": None, "outcome": "ok", "changes": {"version": [1, 2]}, "reason": None})
    def test_r04_everything_else_survives(self):
        hd = self.open_old(); old = v1_document(); doc = self.raw(self.p1)
        self.assertEqual(doc["users"], old["users"]); self.assertEqual(doc["notifications"], old["notifications"]); self.assertEqual(doc["saved_searches"], old["saved_searches"]); self.assertEqual(doc["next"], old["next"])
        for new_t, old_t in zip(doc["tickets"], old["tickets"]):
            for k, v in old_t.items():
                if k != "priority": self.assertEqual(new_t[k], v, k)
    def test_r04_migrated_tickets_work_with_the_old_vocabulary(self):
        hd = self.open_old(); self.assertEqual([hd.tickets.get(i).priority for i in range(1, 6)], ["low", "normal", "high", "urgent", "normal"])
    def test_r04_the_migrated_data_can_be_used_and_audit_continues(self):
        hd = self.open_old(); hd.tickets.add_comment(2, 2, "more"); self.assertEqual(hd.audit.entries()[-1]["seq"], 2); self.assertEqual(hd.tickets.get(2).comments[-1].body, "more"); self.assertEqual(len(hd.tickets.get(2).comments), 2)
    def test_r04_opening_a_version_2_file_changes_nothing(self):
        self.open_old(); before = open(self.p1).read(); os.remove(self.p1 + ".v1.bak"); hd = self.open_old(); self.assertEqual(open(self.p1).read(), before); self.assertFalse(os.path.exists(self.p1 + ".v1.bak")); self.assertEqual(len(hd.audit.entries()), 1)
    def test_r04_the_backup_is_never_overwritten(self):
        self.open_old(); marker = "original-backup"; open(self.p1 + ".v1.bak", "w").write(marker); self.write_v1(self.p1); self.open_old(); self.assertEqual(open(self.p1 + ".v1.bak").read(), marker)
    def test_r04_a_future_version_is_refused_and_untouched(self):
        doc = v1_document(); doc["version"] = 99; p = os.path.join(self.dir, "future.json"); json.dump(doc, open(p, "w")); before = open(p).read()
        with self.assertRaises(StoreError) as cm: Helpdesk(p, self.clock)
        self.assertIn("99", str(cm.exception)); self.assertEqual(open(p).read(), before); self.assertFalse(os.path.exists(p + ".v1.bak"))
    def test_r04_damaged_files_still_raise_store_error(self):
        for junk in ("{nope", "[]", '{"version": 2}', '{"version": 1, "users": 5}'):
            p = os.path.join(self.dir, "bad.json"); open(p, "w").write(junk)
            with self.assertRaises(StoreError): Helpdesk(p, self.clock)
    def test_r04_a_migrated_file_with_no_tickets(self):
        doc = v1_document(); doc["tickets"] = []; p = os.path.join(self.dir, "empty.json"); json.dump(doc, open(p, "w")); Helpdesk(p, self.clock); self.assertEqual(self.raw(p)["tickets"], [])
    def test_r04_store_accepts_a_clock(self):
        s = store_mod.Store(self.p1, clock=lambda: "2030-01-01T00:00:00"); self.assertEqual(s.audit[0]["ts"], "2030-01-01T00:00:00")
    def test_r04_migrating_twice_is_impossible_but_harmless(self): self.open_old(); self.open_old(); self.assertEqual(len(self.raw(self.p1)["audit"]), 1)
    def test_r04_a_v1_file_opened_through_the_command_line(self):
        import io, sys; from helpdesk import cli
        os.environ["HELPDESK_FILE"] = self.p1; os.environ["HELPDESK_NOW"] = "2024-03-01T09:00:00"
        try:
            out = io.StringIO(); self.assertEqual(cli.main(["--as", "1", "ticket", "show", "4"], out), 0)
        finally: os.environ.pop("HELPDESK_FILE"); os.environ.pop("HELPDESK_NOW")
        text = out.getvalue(); self.assertIn("Priority: urgent", text); self.assertIn("Severity: critical", text)
