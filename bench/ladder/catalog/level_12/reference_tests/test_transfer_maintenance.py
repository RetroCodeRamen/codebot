import csv, json, os, threading, time
from support import Base, Clock
from tracker import Conflict, Maintenance, NotFound, PermissionDenied, Tracker, ValidationError

class TestExportImport(Base):
    def setUp(self):
        super().setUp(); self.a = self.issue("First", "bob", priority="high", assignee="carol", labels=["bug", "ui"]); self.t.add_comment(self.a["key"], "me too", "carol"); self.clock.ts = "2024-03-02T09:00:00"; c = self.t.add_comment(self.a["key"], "fixing", "bob"); self.t.edit_comment(c["id"], "fixing now", "bob")
        self.b = self.issue("Second, with comma", "alice"); self.t.transition(self.b["key"], "resolved", "alice", "done"); self.t.transition(self.b["key"], "closed", "alice")
    def test_r11_export_shape(self):
        d = self.t.export_project("WEB", "alice"); self.assertEqual((d["format"], d["version"]), ("tracker-export", 1)); self.assertEqual(d["project"], {"key": "WEB", "name": "Website", "public": True, "archived": False, "created": "2024-03-01T09:00:00"}); self.assertEqual(d["members"], [{"user": "alice", "role": "manager"}, {"user": "bob", "role": "developer"}, {"user": "carol", "role": "reporter"}, {"user": "dave", "role": "developer"}])
        i = d["issues"][0]; self.assertEqual(sorted(i), sorted(["number", "title", "description", "status", "priority", "reporter", "assignee", "resolution", "created", "updated", "closed", "labels", "comments"])); self.assertEqual((i["number"], i["labels"], i["assignee"]), (1, ["bug", "ui"], "carol")); self.assertEqual([c["body"] for c in i["comments"]], ["me too", "fixing now"]); self.assertEqual(sorted(i["comments"][1]), ["author", "body", "created", "edited"])
        self.assertEqual([x["number"] for x in d["issues"]], [1, 2]); self.assertEqual((d["issues"][1]["status"], d["issues"][1]["resolution"], d["issues"][1]["closed"]), ("closed", "done", "2024-03-02T09:00:00"))
    def test_r11_export_is_json_serialisable(self): json.dumps(self.t.export_project("WEB", "root"))
    def test_r11_export_rights(self):
        for who in ("bob", "carol", "eve", "dave"):
            with self.assertRaises(PermissionDenied): self.t.export_project("WEB", who)
        with self.assertRaises(NotFound): self.t.export_project("SEC", "eve")
        self.t.export_project("WEB", "root")
    def test_r11_round_trip_into_a_fresh_tracker(self):
        d = self.t.export_project("WEB", "root"); fresh = Tracker(clock=self.clock); self.addCleanup(fresh.close); fresh.create_user("root", "password1", "admin")
        for n in ("alice", "bob", "carol", "dave"): fresh.create_user(n, "password1", "member", actor="root")
        p = fresh.import_project(d, "root"); self.assertEqual(p, {"key": "WEB", "name": "Website", "public": True, "archived": False, "created": "2024-03-01T09:00:00"}); self.assertEqual(fresh.export_project("WEB", "root"), d)
        self.assertEqual(fresh.get_issue("WEB-1", "bob")["comments"][1]["edited"], "2024-03-02T09:00:00")
    def test_r11_numbering_continues_after_an_import(self):
        d = self.t.export_project("WEB", "root"); fresh = Tracker(clock=self.clock); self.addCleanup(fresh.close); fresh.create_user("root", "password1", "admin")
        for n in ("alice", "bob", "carol", "dave"): fresh.create_user(n, "password1", "member", actor="root")
        fresh.import_project(d, "root"); self.assertEqual(fresh.create_issue("WEB", "new", "d", "bob")["key"], "WEB-3")
    def test_r11_numbers_with_gaps_are_preserved(self):
        d = self.t.export_project("WEB", "root"); d["issues"][1]["number"] = 7; fresh = Tracker(clock=self.clock); self.addCleanup(fresh.close); fresh.create_user("root", "password1", "admin")
        for n in ("alice", "bob", "carol", "dave"): fresh.create_user(n, "password1", "member", actor="root")
        d["project"]["key"] = "NEW"; fresh.import_project(d, "root"); self.assertEqual(fresh.get_issue("NEW-7", "root")["title"], "Second, with comma"); self.assertEqual(fresh.create_issue("NEW", "n", "d", "bob")["key"], "NEW-8")
    def test_r11_import_is_admin_only(self):
        d = self.t.export_project("WEB", "root"); d["project"]["key"] = "AAA"
        with self.assertRaises(PermissionDenied): self.t.import_project(d, "alice")
    def test_r11_an_existing_key_is_a_conflict(self):
        with self.assertRaises(Conflict): self.t.import_project(self.t.export_project("WEB", "root"), "root")
    def test_r11_unknown_users_are_named_and_nothing_is_imported(self):
        d = self.t.export_project("WEB", "root"); d["project"]["key"] = "NEW"; fresh = Tracker(clock=self.clock); self.addCleanup(fresh.close); fresh.create_user("root", "password1", "admin"); fresh.create_user("alice", "password1", actor="root")
        with self.assertRaises(ValidationError) as cm: fresh.import_project(d, "root")
        for n in ("bob", "carol", "dave"): self.assertIn(n, str(cm.exception))
        self.assertEqual(fresh.list_projects("root"), [])
    def test_r11_invalid_data_changes_nothing(self):
        base = self.t.export_project("WEB", "root"); base["project"]["key"] = "NEW"
        def mutated(f): d = json.loads(json.dumps(base)); f(d); return d
        bad = [{}, {"format": "other", "version": 1}, mutated(lambda d: d.update(version=2)), mutated(lambda d: d["project"].update(key="x")), mutated(lambda d: d["issues"][1].update(number=1)), mutated(lambda d: d["issues"][0].update(number=0)), mutated(lambda d: d["issues"][0].update(number=True)),
               mutated(lambda d: d["issues"][0].pop("title")), mutated(lambda d: d["issues"][0]["comments"][0].pop("body")), mutated(lambda d: d.pop("issues")), "text", None]
        for d in bad:
            with self.assertRaises(ValidationError): self.t.import_project(d, "root")
        self.assertEqual([p["key"] for p in self.t.list_projects("root")], ["SEC", "WEB"])
    def test_r11_a_failure_in_the_middle_rolls_everything_back(self):
        d = self.t.export_project("WEB", "root"); d["project"]["key"] = "NEW"; d["issues"][1]["comments"].append({"author": "bob", "body": "x", "created": None, "edited": None})
        with self.assertRaises(ValidationError): self.t.import_project(d, "root")
        with self.assertRaises(NotFound): self.t.get_project("NEW", "root")
        self.assertEqual(self.t.db.execute("SELECT COUNT(*) FROM issues WHERE project_key = 'NEW'").fetchone()[0], 0)
    def test_r11_import_writes_an_audit_entry(self):
        d = self.t.export_project("WEB", "root"); d["project"]["key"] = "NEW"; self.t.import_project(d, "root"); e = self.t.audit_entries("root")[-1]; self.assertEqual((e["action"], e["entity"], e["entity_id"], e["changes"]), ("project.import", "project", "NEW", {"issues": [0, 2]}))
    def test_r11_an_archived_project_can_be_imported_and_stays_archived(self):
        self.t.archive_project("WEB", "root"); d = self.t.export_project("WEB", "root"); self.assertTrue(d["project"]["archived"]); d["project"]["key"] = "ARC"; self.t.import_project(d, "root"); self.assertTrue(self.t.get_project("ARC", "root")["archived"])
    def test_r11_csv_export(self):
        p = self.dir + "/e.csv"; self.assertEqual(self.t.export_csv("WEB", "alice", p), 2); rows = list(csv.reader(open(p)))
        self.assertEqual(rows[0], ["key", "title", "status", "priority", "reporter", "assignee", "labels", "created", "updated"]); self.assertEqual(rows[1], ["WEB-1", "First", "open", "high", "bob", "carol", "bug|ui", "2024-03-01T09:00:00", "2024-03-02T09:00:00"]); self.assertEqual(rows[2][1], "Second, with comma"); self.assertEqual(rows[2][5:7], ["", ""])
        self.assertEqual(sorted(os.listdir(self.dir)), sorted(f for f in os.listdir(self.dir) if not f.startswith(".export")))
    def test_r11_csv_export_rights(self):
        with self.assertRaises(PermissionDenied): self.t.export_csv("WEB", "bob", self.dir + "/x.csv")
        self.assertFalse(os.path.exists(self.dir + "/x.csv"))

class TestMaintenance(Base):
    def setUp(self): super().setUp(); self.m = Maintenance(self.t, close_after_days=14)
    def resolved(self, title="R", actor="bob"):
        i = self.issue(title, actor); self.t.transition(i["key"], "resolved", actor, "fixed"); return i["key"]
    def test_r13_constructor_defaults(self): self.assertEqual(Maintenance(self.t).close_after_days, 14); self.assertEqual((self.m.last_report, self.m.runs, self.m.last_error), (None, 0, None))
    def test_r13_a_run_with_nothing_to_do(self): self.assertEqual(self.m.run_once(), {"closed": [], "tokens_purged": 0, "problems": []}); self.assertEqual((self.m.runs, self.m.last_report), (1, {"closed": [], "tokens_purged": 0, "problems": []}))
    def test_r13_old_resolved_issues_are_closed(self):
        k = self.resolved(); self.clock.ts = "2024-03-15T09:00:01"; r = self.m.run_once(); self.assertEqual(r["closed"], [k]); g = self.t.get_issue(k, "bob"); self.assertEqual((g["status"], g["closed"], g["updated"], g["resolution"]), ("closed", "2024-03-15T09:00:01", "2024-03-15T09:00:01", "fixed"))
    def test_r13_the_age_boundary(self):
        k = self.resolved(); self.clock.ts = "2024-03-15T09:00:00"; self.assertEqual(self.m.run_once()["closed"], []); self.clock.ts = "2024-03-15T09:00:01"; self.assertEqual(self.m.run_once()["closed"], [k])
    def test_r13_only_resolved_issues_are_touched(self):
        o = self.issue("open", "bob")["key"]; p = self.issue("prog", "bob")["key"]; self.t.transition(p, "in_progress", "bob"); c = self.issue("closed", "bob")["key"]; self.t.transition(c, "closed", "bob"); self.clock.ts = "2025-01-01T00:00:00"; self.assertEqual(self.m.run_once()["closed"], [])
        self.assertEqual([self.t.get_issue(k, "bob")["status"] for k in (o, p, c)], ["open", "in_progress", "closed"])
    def test_r13_activity_resets_the_clock(self):
        k = self.resolved(); self.clock.ts = "2024-03-10T09:00:00"; self.t.add_comment(k, "still relevant", "carol"); self.clock.ts = "2024-03-20T09:00:00"; self.assertEqual(self.m.run_once()["closed"], []); self.clock.ts = "2024-03-24T09:00:01"; self.assertEqual(self.m.run_once()["closed"], [k])
    def test_r13_audit_entries_are_written_per_issue_by_the_system(self):
        a, b = self.resolved("A"), self.resolved("B"); self.clock.ts = "2024-04-01T00:00:00"; self.m.run_once(); es = [e for e in self.t.audit_entries("root") if e["actor"] == "system"]; self.assertEqual([(e["action"], e["entity"], e["entity_id"], e["outcome"], e["changes"]) for e in es], [("issue.transition", "issue", a, "ok", {"status": ["resolved", "closed"]}), ("issue.transition", "issue", b, "ok", {"status": ["resolved", "closed"]})])
    def test_r13_close_after_days_is_configurable(self): k = self.resolved(); self.clock.ts = "2024-03-03T09:00:00"; self.assertEqual(Maintenance(self.t, close_after_days=1).run_once()["closed"], [k]); self.assertEqual(Maintenance(self.t, close_after_days=30).run_once()["closed"], [])
    def test_r13_expired_tokens_are_purged(self):
        old = self.t.login("bob", "password1"); self.clock.ts = "2024-03-01T20:00:00"; fresh = self.t.login("carol", "password1"); self.clock.ts = "2024-03-02T09:00:00"; r = self.m.run_once(); self.assertEqual(r["tokens_purged"], 1); self.assertEqual(self.t.authenticate_token(fresh)["name"], "carol")
        e = self.t.audit_entries("root")[-1]; self.assertEqual((e["action"], e["entity"], e["entity_id"], e["actor"], e["changes"]), ("maintenance.purge_tokens", "token", None, "system", {"deleted": [0, 1]}))
    def test_r13_no_audit_entry_when_nothing_was_purged(self): self.t.login("bob", "password1"); n = len(self.t.audit_entries("root")); self.m.run_once(); self.assertEqual(len(self.t.audit_entries("root")), n)
    def test_r13_a_second_run_does_nothing_more(self): self.resolved(); self.clock.ts = "2024-04-01T00:00:00"; self.assertEqual(len(self.m.run_once()["closed"]), 1); self.assertEqual(self.m.run_once(), {"closed": [], "tokens_purged": 0, "problems": []}); self.assertEqual(self.m.runs, 2)
    def test_r13_integrity_of_healthy_data(self): self.issue(); self.assertEqual(self.m.check_integrity(), [])
    def test_r13_integrity_problems(self):
        i = self.issue(); self.t.db.execute("UPDATE projects SET next_number = 1 WHERE key = 'WEB'"); self.t.db.execute("UPDATE issues SET closed = '2024-03-01T00:00:00' WHERE number = 1"); self.t.db.execute("DELETE FROM audit WHERE seq = 3")
        self.assertEqual(self.m.check_integrity(), ["project WEB: next_number 1 is not above the highest issue number 1", "issue WEB-1: closed time and status disagree", "the audit sequence has gaps"])
    def test_r13_a_closed_issue_without_a_closed_time(self): i = self.issue(); self.t.db.execute("UPDATE issues SET status = 'closed' WHERE number = 1"); self.assertEqual(self.m.check_integrity(), ["issue WEB-1: closed time and status disagree"])
    def test_r13_problems_appear_in_the_run_report(self): self.issue(); self.t.db.execute("UPDATE projects SET next_number = 1 WHERE key = 'WEB'"); self.assertEqual(len(self.m.run_once()["problems"]), 1)
    def test_r13_run_once_is_atomic(self):
        k = self.resolved(); self.clock.ts = "2024-04-01T00:00:00"; orig = self.t._audit; calls = {"n": 0}
        def boom(*a, **kw):
            calls["n"] += 1; raise RuntimeError("audit failed")
        self.t._audit = boom
        with self.assertRaises(RuntimeError): self.m.run_once()
        self.t._audit = orig; self.assertEqual(self.t.get_issue(k, "bob")["status"], "resolved"); self.assertEqual(self.m.runs, 0)
    def test_r13_the_background_thread_runs_immediately_and_repeatedly(self):
        self.resolved(); self.clock.ts = "2024-04-01T00:00:00"; self.assertIs(self.m.start(interval=0.05), self.m); end = time.time() + 10
        while self.m.runs < 3 and time.time() < end: time.sleep(0.02)
        self.m.stop(); self.assertGreaterEqual(self.m.runs, 3); self.assertEqual(self.m.last_report["closed"], []); self.assertEqual(len([e for e in self.t.audit_entries("root") if e["actor"] == "system"]), 1)
    def test_r13_start_twice_does_not_start_a_second_thread(self):
        self.m.start(interval=0.05); th = self.m._thread; self.m.start(interval=0.05); self.assertIs(self.m._thread, th); self.m.stop()
    def test_r13_an_error_in_a_run_does_not_stop_the_thread(self):
        orig = self.m.run_once; state = {"n": 0}
        def flaky():
            state["n"] += 1
            if state["n"] == 1: raise RuntimeError("first run explodes")
            return orig()
        self.m.run_once = flaky; self.m.start(interval=0.05); end = time.time() + 10
        while state["n"] < 3 and time.time() < end: time.sleep(0.02)
        self.m.stop(); self.assertGreaterEqual(state["n"], 3); self.assertEqual(self.m.last_error, "RuntimeError: first run explodes"); self.assertGreaterEqual(self.m.runs, 1)
    def test_r13_stop_without_start_and_restart(self): self.m.stop(); self.m.start(interval=0.05); self.m.stop(); self.m.start(interval=0.05); self.m.stop()
    def test_r13_the_thread_works_on_a_file_database_shared_with_other_users(self):
        k = self.resolved(); self.clock.ts = "2024-04-01T00:00:00"; self.m.start(interval=0.05); other = self.open()
        try:
            end = time.time() + 10
            while other.get_issue(k, "bob")["status"] != "closed" and time.time() < end: time.sleep(0.02)
            self.assertEqual(other.get_issue(k, "bob")["status"], "closed")
        finally: self.m.stop(); other.close()

class TestConcurrency(Base):
    def test_r14_eight_threads_create_eighty_unique_issues(self):
        errs, keys, lock = [], [], threading.Lock()
        def work(n):
            tr = self.open()
            try:
                for _ in range(10):
                    k = tr.create_issue("WEB", f"t{n}", "d", "bob")["key"]
                    with lock: keys.append(k)
            except Exception as e: errs.append(e)
            finally: tr.close()
        ts = [threading.Thread(target=work, args=(n,)) for n in range(8)]; [t.start() for t in ts]; [t.join(180) for t in ts]
        self.assertEqual(errs, []); self.assertEqual(sorted(int(k.split("-")[1]) for k in keys), list(range(1, 81))); self.assertEqual(self.t.db.execute("SELECT next_number FROM projects WHERE key = 'WEB'").fetchone()[0], 81)
        self.assertEqual(Maintenance(self.t).check_integrity(), []); self.assertEqual(len([e for e in self.t.audit_entries("root") if e["action"] == "issue.create"]), 80)
    def test_r14_concurrent_comments_labels_and_transitions(self):
        i = self.issue(); errs = []
        def work(n):
            tr = self.open()
            try:
                for j in range(5):
                    tr.add_comment(i["key"], f"c{n}-{j}", "carol"); tr.add_label(i["key"], f"l{n}{j}", "bob")
                tr.transition(i["key"], "in_progress", "bob") if n == 0 else None
            except Exception as e: errs.append(e)
            finally: tr.close()
        ts = [threading.Thread(target=work, args=(n,)) for n in range(2)]; [t.start() for t in ts]; [t.join(180) for t in ts]
        self.assertEqual(errs, []); g = self.t.get_issue(i["key"], "bob"); self.assertEqual((len(g["comments"]), len(g["labels"]), g["status"]), (10, 10, "in_progress")); self.assertEqual(Maintenance(self.t).check_integrity(), [])
    def test_r14_a_failed_operation_does_not_consume_a_number(self):
        for _ in range(3):
            with self.assertRaises(ValidationError): self.t.create_issue("WEB", "", "d", "bob")
            with self.assertRaises(PermissionDenied): self.t.create_issue("WEB", "x", "d", "eve")
        self.assertEqual(self.issue()["key"], "WEB-1"); self.assertEqual(Maintenance(self.t).check_integrity(), [])
    def test_r14_the_audit_log_has_no_gaps_after_mixed_failures(self):
        for _ in range(3):
            with self.assertRaises(PermissionDenied): self.t.create_issue("WEB", "x", "d", "eve")
            with self.assertRaises(ValidationError): self.t.create_issue("WEB", "", "d", "bob")
            self.issue()
        seqs = [e["seq"] for e in self.t.audit_entries("root")]; self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
    def test_r14_concurrent_logins_and_tokens(self):
        toks, lock = [], threading.Lock()
        def work():
            tr = self.open()
            try:
                for _ in range(5):
                    t = tr.login("bob", "password1")
                    with lock: toks.append(t)
            finally: tr.close()
        ts = [threading.Thread(target=work) for _ in range(4)]; [t.start() for t in ts]; [t.join(120) for t in ts]; self.assertEqual(len(set(toks)), 20); self.assertTrue(all(self.t.authenticate_token(x)["name"] == "bob" for x in toks))
