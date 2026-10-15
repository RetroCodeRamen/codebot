import io, os, sys
from support import Base
from helpdesk import cli, housekeeping, permissions, timeline

class Cli(Base):
    def setUp(self): super().setUp(); os.environ["HELPDESK_FILE"] = self.path; os.environ["HELPDESK_NOW"] = "2024-03-01T09:00:00"
    def tearDown(self): os.environ.pop("HELPDESK_FILE", None); os.environ.pop("HELPDESK_NOW", None); super().tearDown()
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO(); old = sys.stderr; sys.stderr = err
        try: rc = cli.main(list(argv), out)
        finally: sys.stderr = old
        return rc, out.getvalue(), err.getvalue()
    def ok(self, *argv): rc, out, err = self.run_cli(*argv); self.assertEqual(rc, 0, (argv, err)); return out

class TestCliNewCommands(Cli):
    def setUp(self): super().setUp(); self.ok("--as", "3", "ticket", "add", "Printer jam", "--description", "stuck"); self.ok("--as", "3", "ticket", "add", "Printer jam again", "--description", "stuck again", "--severity", "high")
    def test_r05_label_commands(self):
        self.assertEqual(self.ok("--as", "2", "ticket", "label", "add", "1", "bug"), "Ticket #1 labels: bug\n"); self.assertEqual(self.ok("--as", "2", "ticket", "label", "add", "1", "ui"), "Ticket #1 labels: bug, ui\n"); self.assertEqual(self.ok("--as", "2", "ticket", "label", "remove", "1", "bug"), "Ticket #1 labels: ui\n"); self.assertEqual(self.ok("--as", "2", "ticket", "label", "remove", "1", "ui"), "Ticket #1 labels: -\n")
    def test_r05_label_errors(self):
        self.assertEqual(self.run_cli("--as", "3", "ticket", "label", "add", "1", "bug")[0], 3); self.assertEqual(self.run_cli("--as", "2", "ticket", "label", "remove", "1", "zzz")[0], 1); self.assertEqual(self.run_cli("--as", "2", "ticket", "label", "add", "1", "bad label")[0], 2)
    def test_r05_show_prints_the_new_lines_after_the_old_ones(self):
        self.ok("--as", "2", "ticket", "label", "add", "1", "bug"); lines = self.ok("--as", "3", "ticket", "show", "1").splitlines()
        self.assertEqual(lines[:9], ["Ticket #1: Printer jam", "Status: open", "Priority: normal", "Category: general", "Requester: Cy (#3)", "Assignee: -", "Created: 2024-03-01T09:00:00", "Updated: 2024-03-01T09:00:00", "SLA due: 2024-03-02T09:00:00"])
        self.assertEqual(lines[9:12], ["Severity: medium", "Labels: bug", ""]); self.assertEqual(lines[12], "stuck")
    def test_r05_show_without_labels_has_no_labels_line(self): self.assertNotIn("Labels:", self.ok("--as", "3", "ticket", "show", "1")); self.assertIn("Severity: medium", self.ok("--as", "3", "ticket", "show", "1"))
    def test_r05_list_filters(self):
        self.ok("--as", "2", "ticket", "label", "add", "2", "bug"); self.assertEqual(self.ok("--as", "1", "ticket", "list", "--label", "bug"), "#2    open        high    Printer jam again\n"); self.assertEqual(self.ok("--as", "1", "ticket", "list", "--severity", "high"), "#2    open        high    Printer jam again\n"); self.assertEqual(self.ok("--as", "1", "ticket", "list", "--priority", "normal"), "#1    open        normal  Printer jam\n")
    def test_r03_severity_command_and_add_option(self):
        self.assertEqual(self.ok("--as", "2", "ticket", "severity", "1", "critical"), "Ticket #1 severity set to critical\n"); self.assertEqual(self.ok("--as", "2", "ticket", "priority", "1", "low"), "Ticket #1 priority set to low\n"); self.assertEqual(self.run_cli("--as", "2", "ticket", "severity", "1", "urgent")[0], 2)
        self.assertIn("Severity: high", self.ok("--as", "3", "ticket", "show", "2"))
    def test_r03_summary_report_lists_severity_and_labels(self):
        self.ok("--as", "2", "ticket", "label", "add", "1", "bug"); text = self.ok("--as", "1", "report", "summary"); self.assertIn("Total: 2", text); self.assertIn("Severity high: 1", text); self.assertIn("Label bug: 1", text); self.assertIn("Priority normal: 1", text)
    def test_r06_merge_command(self):
        self.ok("--as", "3", "ticket", "comment", "2", "same"); self.assertEqual(self.ok("--as", "2", "ticket", "merge", "2", "1"), "Merged ticket #2 into #1 (1 comment(s) moved)\n"); self.assertIn("Merged into: #1", self.ok("--as", "1", "ticket", "show", "2")); self.assertIn("Status: closed", self.ok("--as", "1", "ticket", "show", "2"))
    def test_r06_merge_errors(self):
        self.assertEqual(self.run_cli("--as", "3", "ticket", "merge", "2", "1")[0], 3); self.assertEqual(self.run_cli("--as", "2", "ticket", "merge", "2", "2")[0], 2); self.assertEqual(self.run_cli("--as", "2", "ticket", "merge", "9", "1")[0], 1)
    def test_r02_user_add_by_a_non_admin_is_denied_and_audited(self):
        rc, out, err = self.run_cli("--as", "2", "user", "add", "Eve"); self.assertEqual(rc, 3); e = self.open().audit.entries()[-1]; self.assertEqual((e["action"], e["outcome"], e["actor"]), ("user.create", "denied", 2))
    def test_r02_user_add_by_an_admin_records_the_actor(self): self.ok("--as", "1", "user", "add", "Eve", "--role", "agent"); e = self.open().audit.entries()[-1]; self.assertEqual((e["action"], e["actor"], e["changes"]["name"]), ("user.create", 1, [None, "Eve"]))

class TestAuditCommands(Cli):
    def setUp(self):
        super().setUp(); self.ok("--as", "3", "ticket", "add", "T", "--description", "d"); self.ok("--as", "1", "ticket", "assign", "1", "2"); self.run_cli("--as", "3", "ticket", "assign", "1", "2")
    def test_r07_audit_list_format(self):
        lines = self.ok("--as", "1", "audit", "list").splitlines(); self.assertEqual(lines[-3:], ["#4 2024-03-01T09:00:00 actor=3 ticket.create ticket:1 ok", "#5 2024-03-01T09:00:00 actor=1 ticket.assign ticket:1 ok", "#6 2024-03-01T09:00:00 actor=3 ticket.assign ticket:1 denied"])
    def test_r07_bootstrap_entries_show_a_dash_for_the_actor(self): self.assertEqual(self.ok("--as", "1", "audit", "list", "--limit", "6").splitlines()[0], "#1 2024-03-01T09:00:00 actor=- user.create user:1 ok")
    def test_r07_filters_and_limit(self):
        self.assertEqual(self.ok("--as", "1", "audit", "list", "--outcome", "denied"), "#6 2024-03-01T09:00:00 actor=3 ticket.assign ticket:1 denied\n"); self.assertEqual(len(self.ok("--as", "1", "audit", "list", "--entity", "user").splitlines()), 3)
        self.assertEqual(len(self.ok("--as", "1", "audit", "list", "--action", "ticket.create").splitlines()), 1); self.assertEqual(len(self.ok("--as", "1", "audit", "list", "--actor", "3").splitlines()), 2); self.assertEqual(len(self.ok("--as", "1", "audit", "list", "--entity", "ticket", "--id", "1", "--limit", "2").splitlines()), 2)
    def test_r07_empty_result(self): self.assertEqual(self.ok("--as", "1", "audit", "list", "--action", "nothing"), "No audit entries.\n")
    def test_r07_only_admins_may_read_the_log(self):
        self.assertEqual(self.run_cli("--as", "2", "audit", "list")[0], 3); self.assertEqual(self.run_cli("--as", "3", "audit", "list")[0], 3); self.assertTrue("audit" in permissions.ACTIONS["admin"]); self.assertNotIn("audit", permissions.ACTIONS["agent"])
    def test_r07_reading_the_log_is_not_audited(self): n = len(self.open().audit.entries()); self.ok("--as", "1", "audit", "list"); self.assertEqual(len(self.open().audit.entries()), n)
    def test_r07_report_audit(self):
        text = self.ok("--as", "1", "report", "audit"); self.assertEqual(text.splitlines(), ["ticket.assign: 2", "ticket.create: 1", "user.create: 3"])
    def test_r07_report_audit_needs_the_report_permission(self): self.assertEqual(self.run_cli("--as", "3", "report", "audit")[0], 3)
    def test_r07_report_audit_on_an_empty_log(self):
        os.environ["HELPDESK_FILE"] = os.path.join(self.dir, "fresh.json"); self.ok("user", "add", "Zed", "--role", "admin"); self.assertEqual(self.ok("--as", "1", "report", "audit"), "user.create: 1\n")

class TestTimelineAndHousekeeping(Base):
    def lines(self, t, **kw): return [l.strip() for l in timeline.render(self.hd.tickets.get(t.id), self.hd.store, **kw).splitlines()[1:]]
    def test_r07_timeline_includes_audit_rows(self):
        t = self.ticket(); self.clock.ts = "2024-03-01T10:00:00"; self.hd.tickets.assign(t.id, self.agent.id, self.admin.id); self.clock.ts = "2024-03-01T11:00:00"; self.hd.tickets.set_severity(t.id, "high", self.agent.id)
        self.clock.ts = "2024-03-01T12:00:00"; self.hd.tickets.add_label(t.id, "bug", self.agent.id); self.hd.tickets.remove_label(t.id, "bug", self.agent.id); self.clock.ts = "2024-03-01T13:00:00"; self.hd.tickets.set_status(t.id, "waiting", self.agent.id)
        self.assertEqual(self.lines(t), ["2024-03-01T09:00:00  created by Cy with priority high", "2024-03-01T10:00:00  assigned to Bob by Ada", "2024-03-01T11:00:00  severity changed from medium to high by Bob", "2024-03-01T12:00:00  label bug added by Bob", "2024-03-01T12:00:00  label bug removed by Bob", "2024-03-01T13:00:00  status changed from in_progress to waiting by Bob"])
    def test_r07_closing_is_not_shown_twice(self):
        t = self.ticket(); self.clock.ts = "2024-03-01T12:00:00"; self.hd.tickets.set_status(t.id, "closed", self.agent.id); rows = self.lines(t); self.assertEqual(len(rows), 2); self.assertTrue(rows[1].endswith("closed"))
    def test_r07_a_resolved_then_closed_ticket(self):
        t = self.ticket(); self.clock.ts = "2024-03-01T12:00:00"; self.hd.tickets.set_status(t.id, "resolved", self.agent.id, "fixed"); self.clock.ts = "2024-03-01T13:00:00"; self.hd.tickets.set_status(t.id, "closed", self.agent.id)
        self.assertEqual(self.lines(t)[1:], ["2024-03-01T12:00:00  status changed from open to resolved by Bob", "2024-03-01T13:00:00  closed (fixed)"])
    def test_r07_merge_rows_and_system_actor(self):
        a, b = self.ticket("A"), self.ticket("B"); self.clock.ts = "2024-03-01T12:00:00"; self.hd.tickets.merge(b.id, a.id, self.agent.id); rows = self.lines(b); self.assertIn(f"2024-03-01T12:00:00  merged into #{a.id} by Bob", rows); self.assertEqual(rows[-2:], [f"2024-03-01T12:00:00  closed (merged into #{a.id})", f"2024-03-01T12:00:00  merged into #{a.id} by Bob"])
        self.hd.audit.record(None, "ticket.severity", "ticket", a.id, {"severity": ["low", "high"]}); self.assertIn("2024-03-01T12:00:00  severity changed from low to high by system", self.lines(a))
    def test_r07_denied_and_unrelated_entries_are_not_shown(self):
        t = self.ticket(); other = self.ticket("Other")
        with self.assertRaises(Exception): self.hd.tickets.assign(t.id, self.agent.id, self.cust.id)
        self.hd.tickets.assign(other.id, self.agent.id, self.admin.id); self.assertEqual(len(self.lines(t)), 1)
    def test_r07_internal_comments_stay_hidden_unless_requested(self):
        t = self.ticket(); self.hd.tickets.add_comment(t.id, self.agent.id, "secret", internal=True); self.assertNotIn("secret", "\n".join(self.lines(t))); self.assertIn("secret", "\n".join(self.lines(t, show_internal=True)))
    def test_r07_housekeeping_finds_a_merge_target_that_vanished(self):
        a, b = self.ticket("A"), self.ticket("B"); self.hd.tickets.merge(b.id, a.id, self.agent.id); self.assertEqual(housekeeping.check_integrity(self.hd.store), []); del self.hd.store.tickets[a.id]; self.assertIn(f"ticket #{b.id}: merged into missing ticket #{a.id}", housekeeping.check_integrity(self.hd.store))
    def test_r07_housekeeping_checks_the_audit_sequence(self):
        self.ticket(); self.assertEqual(housekeeping.check_integrity(self.hd.store), []); self.hd.store.audit[2]["seq"] = 9; self.hd.store.audit[3]["seq"] = 9; problems = housekeeping.check_integrity(self.hd.store)
        self.assertEqual([p for p in problems if p.startswith("audit entry")], ["audit entry 3: sequence number is 9"])
