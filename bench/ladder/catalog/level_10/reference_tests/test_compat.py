"""Everything that worked before the change must still work. These tests use only the pre-change interface, so they also pass on the original code."""
import csv, io, json, os, subprocess, sys
from support import Base
from helpdesk import cli, config, export, permissions, reports, sla, workflow
from helpdesk.errors import InvalidTransition, NotFound, PermissionDenied, StoreError, ValidationError
from helpdesk.models import Comment, Ticket, User
from helpdesk.search import parse_query, search
from helpdesk.store import Store

WS = os.getcwd()
HERE = os.path.dirname(os.path.abspath(__file__))

class TestOldPythonInterface(Base):
    def test_r08_create_with_the_old_priority_names(self):
        for p, due in (("urgent", "2024-03-01T13:00:00"), ("high", "2024-03-01T17:00:00"), ("normal", "2024-03-02T09:00:00"), ("low", "2024-03-04T09:00:00")):
            t = self.ticket(priority=p); self.assertEqual((t.priority, t.sla_due), (p, due))
    def test_r08_set_priority_and_list_filter_use_the_old_names(self):
        t = self.ticket(); self.hd.tickets.set_priority(t.id, "urgent", self.agent.id); self.assertEqual(self.hd.tickets.get(t.id).priority, "urgent"); self.assertEqual([x.id for x in self.hd.tickets.list(self.admin.id, priority="urgent")], [t.id]); self.assertEqual(self.hd.tickets.list(self.admin.id, priority="normal"), [])
    def test_r08_invalid_priorities_are_still_validation_errors(self):
        with self.assertRaises(ValidationError): self.hd.tickets.create(self.cust.id, "T", "d", "asap", actor_id=self.cust.id)
        with self.assertRaises(ValidationError): self.hd.tickets.set_priority(self.ticket().id, "asap", self.agent.id)
    def test_r08_config_constants(self): self.assertEqual(config.PRIORITIES, ("low", "normal", "high", "urgent")); self.assertEqual(config.SLA_HOURS, {"urgent": 4, "high": 8, "normal": 24, "low": 72}); self.assertEqual(config.STATUSES, ("open", "in_progress", "waiting", "resolved", "closed")); self.assertEqual(config.ROLES, ("admin", "agent", "customer"))
    def test_r08_sla_functions(self): self.assertEqual(sla.hours_for("urgent"), 4); self.assertEqual(sla.due_for("high", "2024-03-01T09:00:00"), "2024-03-01T17:00:00")
    def test_r08_models_roundtrip(self):
        t = self.ticket(priority="high"); self.hd.tickets.add_comment(t.id, self.agent.id, "x"); t = self.hd.tickets.get(t.id); self.assertEqual(Ticket.from_dict(t.to_dict()), t); u = self.agent; self.assertEqual(User.from_dict(u.to_dict()), u); c = t.comments[0]; self.assertEqual(Comment.from_dict(c.to_dict()), c)
    def test_r08_ticket_dataclass_positional_order(self):
        t = Ticket(1, "T", "d", 3, None, "open", "high", "billing", "c", "u", "s", None, None, []); self.assertEqual((t.status, t.priority, t.category, t.created, t.updated, t.sla_due), ("open", "high", "billing", "c", "u", "s"))
    def test_r08_services_enforce_the_same_permissions(self):
        t = self.ticket()
        for call in (lambda: self.hd.tickets.assign(t.id, self.agent.id, self.cust.id), lambda: self.hd.tickets.set_status(t.id, "closed", self.cust.id), lambda: self.hd.tickets.set_priority(t.id, "high", self.cust.id), lambda: self.hd.users.disable(self.cust.id, self.agent.id)):
            with self.assertRaises(PermissionDenied): call()
    def test_r08_old_permission_table(self):
        for a in ("create_ticket", "comment", "internal_comment", "assign", "change_status", "change_priority", "view_all", "manage_users", "export", "report", "watch", "attach", "purge"): self.assertIn(a, permissions.ACTIONS["admin"], a)
        self.assertEqual(permissions.ACTIONS["customer"] & {"create_ticket", "comment", "watch", "attach"}, {"create_ticket", "comment", "watch", "attach"}); self.assertNotIn("assign", permissions.ACTIONS["customer"]); self.assertNotIn("manage_users", permissions.ACTIONS["agent"]); self.assertNotIn("purge", permissions.ACTIONS["agent"])
    def test_r08_workflow_rules(self):
        t = self.ticket()
        with self.assertRaises(InvalidTransition): self.hd.tickets.set_status(t.id, "open", self.agent.id)
        with self.assertRaises(ValidationError): self.hd.tickets.set_status(t.id, "resolved", self.agent.id)
        self.hd.tickets.set_status(t.id, "resolved", self.agent.id, "done"); self.assertEqual(workflow.allowed("resolved"), ["closed", "open"])
    def test_r08_visibility_rules_for_customers(self):
        other = self.hd.users.create_user("Di", "customer"); mine = self.ticket(); self.ticket(requester=other); self.assertEqual([t.id for t in self.hd.tickets.list(self.cust.id)], [mine.id])
        with self.assertRaises(NotFound): self.hd.tickets.get(2, self.cust.id)
    def test_r08_notifications_still_work(self):
        t = self.ticket(); self.hd.tickets.assign(t.id, self.agent.id, self.admin.id); self.assertEqual([n["kind"] for n in self.hd.notifier.unread(self.agent.id)], ["assigned"]); self.hd.tickets.add_comment(t.id, self.agent.id, "hi"); self.assertEqual([n["kind"] for n in self.hd.notifier.unread(self.cust.id)], ["comment"])
    def test_r08_search_language_for_the_old_fields(self):
        a = self.ticket("Login fails", priority="high"); self.ticket("Invoice", category="billing"); ts = list(self.hd.store.tickets.values())
        self.assertEqual([t.id for t in search(ts, "priority:high")], [a.id]); self.assertEqual([t.id for t in search(ts, "category:billing")], [2]); self.assertEqual([t.id for t in search(ts, "login")], [a.id]); self.assertEqual(parse_query("status:Open x").filters, {"status": "open"})
    def test_r08_summary_keeps_its_old_keys(self):
        self.ticket(priority="urgent"); r = reports.summary(self.hd.store, "2024-03-01T10:00:00"); self.assertEqual((r["total"], r["open_count"], r["by_status"], r["by_priority"], r["by_category"]), (1, 1, {"open": 1}, {"urgent": 1}, {"general": 1}))
    def test_r08_store_basics(self):
        self.ticket(); s = Store(self.path); self.assertEqual(sorted(s.users), [1, 2, 3]); self.assertEqual(s.next["ticket"], 2); self.assertEqual(s.ticket(1).title, "Printer broken")
        with self.assertRaises(NotFound): s.ticket(9)
    def test_r08_a_damaged_data_file_is_still_a_store_error(self):
        open(self.path, "w").write("{nope")
        with self.assertRaises(StoreError): Store(self.path)
    def test_r08_saves_are_atomic_and_leave_no_temporary_files(self): self.ticket(); self.assertEqual(sorted(os.listdir(self.dir)), ["hd.json"])
    def test_r08_csv_export_keeps_the_original_columns_in_front(self):
        self.ticket("Comma, here", priority="high"); p = self.dir + "/t.csv"; self.assertEqual(export.export_csv(self.hd.store, p), 1); rows = list(csv.reader(open(p)))
        self.assertEqual(rows[0][:10], ["id", "title", "status", "priority", "category", "requester", "assignee", "created", "updated", "sla_due"]); self.assertEqual(rows[1][:10], ["1", "Comma, here", "open", "high", "general", "Cy", "", "2024-03-01T09:00:00", "2024-03-01T09:00:00", "2024-03-01T17:00:00"]); self.assertEqual(export.CSV_COLUMNS[:10], rows[0][:10])
    def test_r08_json_export_keeps_the_original_keys(self):
        self.ticket(priority="high"); p = self.dir + "/t.json"; export.export_json(self.hd.store, p); t = json.load(open(p))["tickets"][0]
        for k in ("id", "title", "description", "requester_id", "assignee_id", "status", "priority", "category", "created", "updated", "sla_due", "closed_at", "resolution", "comments", "watchers", "attachments", "links"): self.assertIn(k, t, k)
        self.assertEqual(t["priority"], "high")
    def test_r08_retention_and_bulk_keep_working(self):
        a = self.ticket("A"); self.hd.tickets.set_status(a.id, "waiting", self.agent.id); self.clock.ts = "2024-06-01T00:00:00"; self.assertEqual(self.hd.bulk.close_stale(10, self.agent.id), [a.id]); self.clock.ts = "2024-09-01T00:00:00"; self.assertEqual(self.hd.retention.purge(30, self.admin.id), [a.id]); self.assertEqual(self.hd.retention.archived()[0]["id"], a.id)
    def test_r08_rules_templates_import_balancer_links_saved_searches(self):
        t = self.ticket(); json.dump([{"name": "r", "when": {"priority": "normal"}, "then": {"priority": "high", "assign": "Bob"}}], open(self.path + ".rules.json", "w")); self.assertEqual(len(self.hd.rules.apply(t.id, self.admin.id)), 2); self.assertEqual(self.hd.tickets.get(t.id).priority, "high")
        self.assertTrue(self.hd.replier.reply(t.id, "received", self.agent.id)); self.assertIn(self.hd.balancer.suggest(t.id), (self.admin.id, self.agent.id)); self.hd.links.link(t.id, "related", self.ticket("B").id, self.agent.id); self.assertEqual(len(self.hd.links.outgoing(t.id)), 1)
        self.hd.saved.save(self.agent.id, "q", "priority:high"); self.assertEqual([x.id for x in self.hd.saved.run(self.agent.id, "q")], [t.id])

class TestOldCommandLine(Base):
    def setUp(self): super().setUp(); os.environ["HELPDESK_FILE"] = self.path; os.environ["HELPDESK_NOW"] = "2024-03-01T09:00:00"
    def tearDown(self): os.environ.pop("HELPDESK_FILE", None); os.environ.pop("HELPDESK_NOW", None); super().tearDown()
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO(); old = sys.stderr; sys.stderr = err
        try: rc = cli.main(list(argv), out)
        finally: sys.stderr = old
        return rc, out.getvalue(), err.getvalue()
    def ok(self, *argv): rc, out, err = self.run_cli(*argv); self.assertEqual(rc, 0, (argv, err)); return out
    def test_r08_the_existing_commands_print_what_they_always_printed(self):
        self.assertEqual(self.ok("--as", "3", "ticket", "add", "Printer jam", "--description", "stuck", "--priority", "high"), "Created ticket #1: Printer jam\n"); self.assertEqual(self.ok("--as", "3", "ticket", "list"), "#1    open        high    Printer jam\n")
        self.assertEqual(self.ok("--as", "1", "ticket", "assign", "1", "2"), "Assigned ticket #1 to Bob\n"); self.assertEqual(self.ok("--as", "2", "ticket", "priority", "1", "urgent"), "Ticket #1 priority set to urgent\n"); self.assertEqual(self.ok("--as", "2", "ticket", "comment", "1", "on it"), "Added comment 1 to ticket #1\n")
        self.assertEqual(self.ok("--as", "2", "ticket", "status", "1", "resolved", "--resolution", "done"), "Ticket #1 is now resolved\n"); self.assertEqual(self.ok("--as", "1", "search", "printer"), "#1    resolved    urgent  Printer jam\n")
    def test_r08_show_keeps_its_first_lines(self):
        self.ok("--as", "3", "ticket", "add", "Printer jam", "--description", "stuck", "--priority", "high"); lines = self.ok("--as", "3", "ticket", "show", "1").splitlines(); self.assertEqual(lines[:9], ["Ticket #1: Printer jam", "Status: open", "Priority: high", "Category: general", "Requester: Cy (#3)", "Assignee: -", "Created: 2024-03-01T09:00:00", "Updated: 2024-03-01T09:00:00", "SLA due: 2024-03-01T17:00:00"])
        self.assertIn("stuck", lines)
    def test_r08_exit_codes_are_unchanged(self):
        self.assertEqual(self.run_cli("--as", "1", "ticket", "show", "9")[0], 1); self.ok("--as", "3", "ticket", "add", "T", "--description", "d"); self.assertEqual(self.run_cli("--as", "3", "ticket", "assign", "1", "2")[0], 3); self.assertEqual(self.run_cli("--as", "1", "ticket", "status", "1", "weird")[0], 2); self.assertEqual(self.run_cli()[0], 2); self.assertEqual(self.run_cli("--as", "1", "ticket", "list", "--priority", "asap")[0], 0)
    def test_r08_user_commands(self):
        self.assertIn("Added user 4: Eve (agent)", self.ok("--as", "1", "user", "add", "Eve", "--role", "agent")); self.assertEqual(self.run_cli("--as", "2", "user", "add", "X")[0], 3); self.assertEqual(self.ok("user", "list").splitlines()[0], "  1 Ada (admin)")
    def test_r08_the_first_user_can_be_created_without_an_actor(self):
        os.environ["HELPDESK_FILE"] = os.path.join(self.dir, "fresh.json"); self.assertEqual(self.ok("user", "add", "Zed", "--role", "admin"), "Added user 1: Zed (admin)\n"); self.assertEqual(self.run_cli("user", "add", "Yan")[0], 2)
    def test_r08_reports_exports_and_notifications(self):
        self.ok("--as", "3", "ticket", "add", "T", "--description", "d"); self.ok("--as", "1", "ticket", "assign", "1", "2"); text = self.ok("--as", "1", "report", "summary"); self.assertIn("Total: 1", text); self.assertIn("Priority normal: 1", text); self.assertIn("Status in_progress: 1", text)
        self.assertEqual(self.ok("--as", "1", "report", "workload"), "Bob: 1\n"); self.assertIn("assigned to you", self.ok("--as", "2", "notifications")); p = self.dir + "/x.csv"; self.assertEqual(self.ok("--as", "1", "export", "csv", p), f"Exported 1 ticket(s) to {p}\n")
    def test_r08_watch_attach_label_free_commands(self):
        self.ok("--as", "3", "ticket", "add", "T", "--description", "d"); self.assertEqual(self.ok("--as", "3", "ticket", "watch", "1"), "User 3 now watches ticket #1\n"); self.assertEqual(self.ok("--as", "3", "ticket", "attach", "1", "a.txt", "--size", "5"), "Attached a.txt (5 bytes) to ticket #1\n")
    def test_r08_other_existing_commands(self):
        self.ok("--as", "3", "ticket", "add", "T", "--description", "d"); self.assertEqual(self.ok("--as", "1", "escalate", "--dry-run"), "Nothing to escalate.\n"); self.assertEqual(self.ok("--as", "1", "bulk", "close-stale", "30"), "Closed 0 ticket(s)\n"); self.assertIn("received", self.ok("--as", "2", "template", "list")); self.assertEqual(self.ok("--as", "1", "check"), "No problems found.\n")
    def test_r08_a_new_file_is_created_at_the_current_format(self): self.ok("--as", "3", "ticket", "add", "T", "--description", "d"); self.assertIn("tickets", json.load(open(self.path)))

class TestOriginalSuite(Base):
    def test_r08_the_original_unit_tests_pass_unmodified(self):
        env = dict(os.environ, PYTHONPATH=WS); p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", os.path.join(HERE, "original")], capture_output=True, text=True, env=env, cwd=self.dir, timeout=300)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:]); self.assertIn("Ran 138 tests", p.stderr)
