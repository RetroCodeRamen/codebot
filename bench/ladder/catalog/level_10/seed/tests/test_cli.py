import io, os, sys
from base import Base
from helpdesk import cli

class TestCli(Base):
    def setUp(self):
        super().setUp(); os.environ["HELPDESK_FILE"] = self.path; os.environ["HELPDESK_NOW"] = "2024-03-01T09:00:00"
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO(); old = sys.stderr; sys.stderr = err
        try: rc = cli.main(list(argv), out)
        finally: sys.stderr = old
        return rc, out.getvalue(), err.getvalue()
    def ok(self, *argv): rc, out, err = self.run_cli(*argv); self.assertEqual(rc, 0, (argv, err)); return out
    def test_ticket_add_list_show(self):
        self.assertEqual(self.ok("--as", "3", "ticket", "add", "Printer jam", "--description", "paper stuck", "--priority", "high"), "Created ticket #1: Printer jam\n")
        self.assertEqual(self.ok("--as", "3", "ticket", "list"), "#1    open        high    Printer jam\n")
        shown = self.ok("--as", "3", "ticket", "show", "1").splitlines(); self.assertEqual(shown[:5], ["Ticket #1: Printer jam", "Status: open", "Priority: high", "Category: general", "Requester: Cy (#3)"]); self.assertIn("Assignee: -", shown)
    def test_assign_status_priority_comment(self):
        self.ok("--as", "3", "ticket", "add", "T", "--description", "d")
        self.assertEqual(self.ok("--as", "1", "ticket", "assign", "1", "2"), "Assigned ticket #1 to Bob\n"); self.assertEqual(self.ok("--as", "2", "ticket", "priority", "1", "urgent"), "Ticket #1 priority set to urgent\n")
        self.assertEqual(self.ok("--as", "2", "ticket", "comment", "1", "on it"), "Added comment 1 to ticket #1\n"); self.assertEqual(self.ok("--as", "2", "ticket", "status", "1", "resolved", "--resolution", "done"), "Ticket #1 is now resolved\n")
    def test_search(self): self.ok("--as", "3", "ticket", "add", "Printer jam", "--description", "x"); self.assertEqual(self.ok("--as", "1", "search", "printer"), "#1    open        normal  Printer jam\n"); self.assertEqual(self.ok("--as", "1", "search", "zzz"), "No matches.\n")
    def test_exit_codes(self):
        self.assertEqual(self.run_cli("--as", "1", "ticket", "show", "9")[0], 1)
        self.ok("--as", "3", "ticket", "add", "T", "--description", "d"); self.assertEqual(self.run_cli("--as", "3", "ticket", "assign", "1", "2")[0], 3); self.assertEqual(self.run_cli("--as", "1", "ticket", "status", "1", "weird")[0], 2); self.assertEqual(self.run_cli()[0], 2)
    def test_actor_from_environment(self): os.environ["HELPDESK_USER"] = "3"; self.assertEqual(self.ok("ticket", "add", "T", "--description", "d"), "Created ticket #1: T\n")
    def test_missing_actor(self): rc, out, err = self.run_cli("ticket", "list"); self.assertEqual(rc, 2); self.assertIn("--as", err)
    def test_report_export_and_notifications(self):
        self.ok("--as", "3", "ticket", "add", "T", "--description", "d"); self.ok("--as", "1", "ticket", "assign", "1", "2")
        self.assertIn("Total: 1", self.ok("--as", "1", "report", "summary")); self.assertEqual(self.ok("--as", "1", "report", "workload"), "Bob: 1\n"); self.assertIn("assigned to you", self.ok("--as", "2", "notifications"))
        p = self.dir + "/x.csv"; self.assertEqual(self.ok("--as", "1", "export", "csv", p), f"Exported 1 ticket(s) to {p}\n")
    def test_user_commands(self):
        self.assertIn("Added user 4: Eve (agent)", self.ok("--as", "1", "user", "add", "Eve", "--role", "agent")); self.assertEqual(self.run_cli("--as", "2", "user", "add", "X")[0], 3); self.assertIn("  4 Eve (agent)", self.ok("user", "list"))
    def test_template_and_digest(self):
        self.ok("--as", "3", "ticket", "add", "T", "--description", "d"); self.assertEqual(self.ok("--as", "2", "template", "reply", "1", "received"), "Added comment 1 to ticket #1\n"); self.assertIn("received", self.ok("--as", "2", "template", "list")); self.assertIn("Digest for Cy", self.ok("--as", "3", "digest"))
    def test_escalate_and_bulk(self):
        self.ok("--as", "3", "ticket", "add", "T", "--description", "d"); self.assertEqual(self.ok("--as", "1", "escalate", "--dry-run"), "Nothing to escalate.\n"); self.assertEqual(self.ok("--as", "1", "bulk", "close-stale", "30"), "Closed 0 ticket(s)\n")
