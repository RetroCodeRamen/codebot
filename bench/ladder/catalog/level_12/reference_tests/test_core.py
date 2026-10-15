import sqlite3
from support import Base, Clock
from tracker import AuthError, Conflict, NotFound, PermissionDenied, Tracker, TrackerError, ValidationError

class TestPackage(Base):
    def test_r01_exceptions_and_exports(self):
        import tracker
        for n in ("Tracker", "Maintenance", "TrackerError", "AuthError", "PermissionDenied", "NotFound", "ValidationError", "Conflict"): self.assertTrue(hasattr(tracker, n), n)
        for c in (AuthError, PermissionDenied, NotFound, ValidationError, Conflict): self.assertTrue(issubclass(c, TrackerError), c)
        self.assertTrue(issubclass(ValidationError, ValueError)); self.assertTrue(issubclass(TrackerError, Exception))
    def test_r01_context_manager_and_close(self):
        with Tracker(self.path + "2") as t: t.create_user("x1", "password1", "admin")
        with self.assertRaises(sqlite3.ProgrammingError): t.get_user("x1")
    def test_r01_data_survives_reopening(self):
        i = self.issue(); self.t.close(); t2 = self.open(); self.assertEqual(t2.get_issue(i["key"], "bob")["title"], "Broken login"); self.assertEqual([p["key"] for p in t2.list_projects("root")], ["SEC", "WEB"]); self.assertEqual(t2.login("bob", "password1") is not None, True); t2.close(); self.t = self.open()
    def test_r01_memory_database_works(self):
        with Tracker() as t: t.create_user("root", "password1", "admin"); self.assertEqual(t.get_user("root")["role"], "admin")
    def test_r01_foreign_keys_are_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError): self.t.db.execute("INSERT INTO members(project_key, user_id, role) VALUES ('NOPE', 1, 'manager')")
    def test_r01_a_failed_operation_changes_nothing(self):
        n = self.t.db.execute("SELECT next_number FROM projects WHERE key = 'WEB'").fetchone()[0]
        with self.assertRaises(ValidationError): self.t.create_issue("WEB", "  ", "d", "bob")
        with self.assertRaises(PermissionDenied): self.t.create_issue("WEB", "x", "d", "eve")
        self.assertEqual(self.t.db.execute("SELECT next_number FROM projects WHERE key = 'WEB'").fetchone()[0], n); self.assertEqual(self.t.search("", "root")["total"], 0)
    def test_r01_the_busy_timeout_is_at_least_thirty_seconds(self): self.assertGreaterEqual(self.t.db.execute("PRAGMA busy_timeout").fetchone()[0], 30000)

class TestUsers(Base):
    def test_r02_create_user_returns_the_dict(self): u = self.t.create_user("Frank.Z", "password1", "member", actor="root"); self.assertEqual((u["name"], u["role"], u["active"], u["created"]), ("Frank.Z", "member", True, "2024-03-01T09:00:00")); self.assertIsInstance(u["id"], int)
    def test_r02_names_are_unique_ignoring_case_and_keep_their_spelling(self):
        with self.assertRaises(Conflict): self.t.create_user("ALICE", "password1", "member", actor="root")
        self.assertEqual(self.t.get_user("ALICE")["name"], "alice")
    def test_r02_invalid_names(self):
        for bad in ("", "a", "x" * 31, "1abc", "a b", "a@b", None, 5): 
            with self.assertRaises(ValidationError, msg=repr(bad)): self.t.create_user(bad, "password1", "member", actor="root")
    def test_r02_name_boundaries(self): self.t.create_user("ab", "password1", "member", actor="root"); self.t.create_user("a" + "b" * 29, "password1", "member", actor="root"); self.t.create_user("a.b_c-d", "password1", "member", actor="root")
    def test_r02_invalid_passwords_and_roles(self):
        for pw in ("short", "", None, 12345678):
            with self.assertRaises(ValidationError): self.t.create_user("zed", pw, "member", actor="root")
        with self.assertRaises(ValidationError): self.t.create_user("zed", "password1", "boss", actor="root")
        self.t.create_user("zed", "12345678", "member", actor="root")
    def test_r02_passwords_are_not_stored_in_clear_text(self):
        self.assertNotIn("password1", " ".join(str(r) for r in self.t.db.execute("SELECT * FROM users"))); h = [r[0] for r in self.t.db.execute("SELECT pw_hash FROM users")]; self.assertEqual(len(set(h)), len(h))
    def test_r02_the_first_user_must_be_an_admin_and_needs_no_actor(self):
        with Tracker() as t:
            with self.assertRaises(ValidationError): t.create_user("x1", "password1", "member")
            t.create_user("x1", "password1", "admin")
            with self.assertRaises(PermissionDenied): t.create_user("x2", "password1", "member")
    def test_r02_only_active_admins_create_users(self):
        for who in ("alice", "bob", "dave"):
            with self.assertRaises(PermissionDenied): self.t.create_user("new" + who, "password1", "member", actor=who)
    def test_r02_get_user_and_list(self):
        with self.assertRaises(NotFound): self.t.get_user("nobody")
        self.assertEqual([u["name"] for u in self.t.list_users("bob")], ["root", "alice", "bob", "carol", "eve", "dave"])
    def test_r02_set_active_is_admin_only(self):
        with self.assertRaises(PermissionDenied): self.t.set_active("bob", False, "alice")
        self.assertFalse(self.t.set_active("bob", False, "root")["active"]); self.assertTrue(self.t.set_active("bob", True, "root")["active"])
    def test_r02_a_disabled_user_cannot_log_in_or_act(self):
        tok = self.t.login("bob", "password1"); self.t.set_active("bob", False, "root")
        with self.assertRaises(AuthError): self.t.login("bob", "password1")
        with self.assertRaises(AuthError): self.t.authenticate_token(tok)
        for call in (lambda: self.t.create_issue("WEB", "x", "d", "bob"), lambda: self.t.list_projects("bob"), lambda: self.t.search("", "bob"), lambda: self.t.get_project("WEB", "bob")):
            with self.assertRaises(PermissionDenied): call()
    def test_r02_reenabling_restores_access(self): self.t.set_active("bob", False, "root"); self.t.set_active("bob", True, "root"); self.assertTrue(self.t.login("bob", "password1")); self.assertTrue(self.issue())
    def test_r02_unknown_actor(self):
        with self.assertRaises(NotFound): self.t.list_projects("ghost")

class TestAuth(Base):
    def test_r03_login_returns_a_long_random_token(self): a, b = self.t.login("bob", "password1"), self.t.login("bob", "password1"); self.assertGreaterEqual(len(a), 32); self.assertNotEqual(a, b)
    def test_r03_authenticate(self): tok = self.t.login("BOB", "password1"); u = self.t.authenticate_token(tok); self.assertEqual((u["name"], u["role"]), ("bob", "member"))
    def test_r03_one_error_for_all_failures(self):
        for name, pw in (("bob", "wrongpass1"), ("nobody", "password1"), ("bob", ""), (None, "password1")):
            with self.assertRaises(AuthError) as cm: self.t.login(name, pw)
            self.assertEqual(str(cm.exception), "invalid credentials")
    def test_r03_tokens_expire_after_24_hours(self):
        tok = self.t.login("bob", "password1"); self.clock.ts = "2024-03-02T08:59:59"; self.t.authenticate_token(tok); self.clock.ts = "2024-03-02T09:00:00"
        with self.assertRaises(AuthError): self.t.authenticate_token(tok)
    def test_r03_logout(self):
        tok = self.t.login("bob", "password1"); self.t.logout(tok)
        with self.assertRaises(AuthError): self.t.authenticate_token(tok)
        self.t.logout(tok)
    def test_r03_invalid_tokens(self):
        for bad in ("", "nope", None, 5):
            with self.assertRaises(AuthError): self.t.authenticate_token(bad)
    def test_r03_each_token_is_independent(self): a, b = self.t.login("bob", "password1"), self.t.login("bob", "password1"); self.t.logout(a); self.assertEqual(self.t.authenticate_token(b)["name"], "bob")
    def test_r03_tokens_survive_reopening(self): tok = self.t.login("bob", "password1"); self.t.close(); self.t = self.open(); self.assertEqual(self.t.authenticate_token(tok)["name"], "bob")

class TestProjects(Base):
    def test_r04_create_project(self): p = self.t.create_project("ABC1", " Alpha ", "root"); self.assertEqual(p, {"key": "ABC1", "name": "Alpha", "public": False, "archived": False, "created": "2024-03-01T09:00:00"})
    def test_r04_only_admins_create_projects(self):
        with self.assertRaises(PermissionDenied): self.t.create_project("NEW", "n", "alice")
    def test_r04_validation_and_conflict(self):
        for key in ("", "A", "a1", "1AB", "ABCDEFGHIJK", "AB-C", None):
            with self.assertRaises(ValidationError, msg=repr(key)): self.t.create_project(key, "n", "root")
        for name in ("", "  ", "x" * 101, None):
            with self.assertRaises(ValidationError): self.t.create_project("OK", name, "root")
        with self.assertRaises(Conflict): self.t.create_project("WEB", "again", "root"); self.t.create_project("AB", "x" * 100, "root")
    def test_r04_public_projects_are_visible_to_everyone_private_ones_to_members_and_admins(self):
        self.assertEqual([p["key"] for p in self.t.list_projects("eve")], ["WEB"]); self.assertEqual([p["key"] for p in self.t.list_projects("alice")], ["SEC", "WEB"]); self.assertEqual([p["key"] for p in self.t.list_projects("root")], ["SEC", "WEB"]); self.assertEqual([p["key"] for p in self.t.list_projects("bob")], ["WEB"])
    def test_r04_invisible_projects_do_not_exist_for_everything(self):
        i = self.issue("Secret", "alice", "SEC")
        for call in (lambda: self.t.get_project("SEC", "eve"), lambda: self.t.get_issue(i["key"], "eve"), lambda: self.t.create_issue("SEC", "x", "d", "eve"), lambda: self.t.add_comment(i["key"], "x", "eve"), lambda: self.t.transition(i["key"], "closed", "eve"), lambda: self.t.add_label(i["key"], "x", "eve"),
                     lambda: self.t.add_member("SEC", "eve", "reporter", "eve"), lambda: self.t.members("SEC", "eve"), lambda: self.t.project_stats("SEC", "eve"), lambda: self.t.export_project("SEC", "eve"), lambda: self.t.update_issue(i["key"], "eve", title="x"), lambda: self.t.archive_project("SEC", "eve")):
            with self.assertRaises(NotFound): call()
    def test_r04_add_member_changes_the_role_and_returns_it(self):
        self.assertEqual(self.t.add_member("WEB", "eve", "reporter", "alice"), {"user": "eve", "role": "reporter"}); self.assertEqual(self.t.add_member("WEB", "eve", "developer", "alice"), {"user": "eve", "role": "developer"}); self.assertEqual([m for m in self.t.members("WEB", "eve") if m["user"] == "eve"], [{"user": "eve", "role": "developer"}])
    def test_r04_members_are_sorted_by_name(self): self.assertEqual([m["user"] for m in self.t.members("WEB", "bob")], ["alice", "bob", "carol", "dave"])
    def test_r04_only_managers_and_admins_manage_members(self):
        for who in ("bob", "carol", "dave"):
            with self.assertRaises(PermissionDenied): self.t.add_member("WEB", "eve", "reporter", who)
        with self.assertRaises(PermissionDenied): self.t.remove_member("WEB", "bob", "bob")
    def test_r04_invalid_roles_and_unknown_users(self):
        with self.assertRaises(ValidationError): self.t.add_member("WEB", "eve", "boss", "alice")
        with self.assertRaises(NotFound): self.t.add_member("WEB", "nobody", "reporter", "alice")
    def test_r04_remove_member(self):
        self.t.remove_member("WEB", "bob", "alice"); self.assertNotIn("bob", [m["user"] for m in self.t.members("WEB", "alice")])
        with self.assertRaises(NotFound): self.t.remove_member("WEB", "bob", "alice")
        with self.assertRaises(PermissionDenied): self.issue("x", "bob")
    def test_r04_archive_makes_the_project_read_only_for_everyone(self):
        i = self.issue(); self.assertTrue(self.t.archive_project("WEB", "root")["archived"])
        for call in (lambda: self.t.create_issue("WEB", "x", "d", "root"), lambda: self.t.update_issue(i["key"], "root", title="x"), lambda: self.t.transition(i["key"], "closed", "root"), lambda: self.t.add_comment(i["key"], "x", "root"), lambda: self.t.add_label(i["key"], "x", "root"), lambda: self.t.add_member("WEB", "eve", "reporter", "root")):
            with self.assertRaises(PermissionDenied): call()
        self.assertEqual(self.t.get_issue(i["key"], "carol")["title"], "Broken login"); self.assertTrue(self.t.export_project("WEB", "alice"))
    def test_r04_archiving_is_for_admins_only(self):
        with self.assertRaises(PermissionDenied): self.t.archive_project("WEB", "alice")
    def test_r04_get_project_returns_the_public_dict(self): self.assertEqual(sorted(self.t.get_project("WEB", "eve")), ["archived", "created", "key", "name", "public"])

class TestPermissions(Base):
    def test_r05_matrix_for_issue_writes(self):
        i = self.issue("Mine", "carol")
        self.assertEqual(self.t.update_issue(i["key"], "carol", title="Mine edited")["title"], "Mine edited"); self.assertEqual(self.t.update_issue(i["key"], "carol", description="new")["description"], "new")
        for f in ({"priority": "high"}, {"assignee": "bob"}):
            with self.assertRaises(PermissionDenied): self.t.update_issue(i["key"], "carol", **f)
        with self.assertRaises(PermissionDenied): self.t.transition(i["key"], "closed", "carol")
        with self.assertRaises(PermissionDenied): self.t.add_label(i["key"], "x", "carol")
    def test_r05_a_reporter_cannot_edit_someone_elses_issue(self):
        i = self.issue("Bobs", "bob")
        with self.assertRaises(PermissionDenied): self.t.update_issue(i["key"], "carol", title="x")
    def test_r05_a_developer_edits_any_issue_and_the_priority(self):
        i = self.issue("Carols", "carol"); u = self.t.update_issue(i["key"], "bob", title="Edited", priority="critical", assignee="bob"); self.assertEqual((u["title"], u["priority"], u["assignee"]), ("Edited", "critical", "bob"))
    def test_r05_managers_do_everything(self):
        i = self.issue("X", "carol"); self.t.update_issue(i["key"], "alice", priority="low", assignee="bob"); self.t.transition(i["key"], "in_progress", "alice"); self.t.add_label(i["key"], "ui", "alice"); self.t.add_comment(i["key"], "hello", "alice"); self.t.export_project("WEB", "alice")
    def test_r05_visitors_and_roleless_members_only_read(self):
        i = self.issue()
        for who in ("eve", "dave"):
            self.assertEqual(self.t.get_issue(i["key"], who)["key"], i["key"])
            for call in (lambda: self.t.create_issue("WEB", "x", "d", who), lambda: self.t.add_comment(i["key"], "x", who), lambda: self.t.transition(i["key"], "closed", who), lambda: self.t.add_label(i["key"], "x", who)):
                with self.assertRaises(PermissionDenied): call()
    def test_r05_global_viewers_never_write_even_with_a_project_role(self):
        i = self.issue()
        with self.assertRaises(PermissionDenied): self.t.add_comment(i["key"], "x", "dave")
        with self.assertRaises(PermissionDenied): self.t.update_issue(i["key"], "dave", title="x")
    def test_r05_admins_write_everywhere(self): i = self.issue("S", "alice", "SEC"); self.t.add_comment(i["key"], "root here", "root"); self.t.transition(i["key"], "in_progress", "root"); self.t.create_issue("WEB", "x", "d", "root")
    def test_r05_only_managers_and_admins_export(self):
        for who in ("bob", "carol", "eve"):
            with self.assertRaises(PermissionDenied): self.t.export_project("WEB", who)
    def test_r05_the_message_names_the_actor(self):
        with self.assertRaises(PermissionDenied) as cm: self.t.create_issue("WEB", "x", "d", "eve")
        self.assertIn("eve", str(cm.exception))
    def test_r05_assigning_needs_the_assign_right_also_at_creation(self):
        with self.assertRaises(PermissionDenied): self.t.create_issue("WEB", "x", "d", "carol", assignee="bob")
        self.assertEqual(self.t.create_issue("WEB", "x", "d", "bob", assignee="carol")["assignee"], "carol")
    def test_r05_labels_at_creation_need_the_label_right(self):
        with self.assertRaises(PermissionDenied): self.t.create_issue("WEB", "x", "d", "carol", labels=["bug"])
        self.assertEqual(self.t.create_issue("WEB", "x", "d", "bob", labels=["Bug", "ui", "bug"])["labels"], ["bug", "ui"])

class TestIssues(Base):
    def test_r06_create_returns_the_documented_dict(self):
        i = self.issue("Broken login", "bob", priority="high", assignee="carol", labels=["bug"])
        self.assertEqual(i, {"key": "WEB-1", "project": "WEB", "number": 1, "title": "Broken login", "description": "details", "status": "open", "priority": "high", "reporter": "bob", "assignee": "carol", "resolution": None, "created": "2024-03-01T09:00:00", "updated": "2024-03-01T09:00:00", "closed": None, "labels": ["bug"]})
    def test_r06_numbers_are_per_project_and_never_repeat(self): a, b, c = self.issue(), self.issue(), self.issue("s", "alice", "SEC"); self.assertEqual((a["key"], b["key"], c["key"]), ("WEB-1", "WEB-2", "SEC-1"))
    def test_r06_defaults(self): i = self.t.create_issue("WEB", "T", actor="bob"); self.assertEqual((i["description"], i["priority"], i["assignee"], i["labels"]), ("", "medium", None, []))
    def test_r06_validation(self):
        for kw in ({"title": ""}, {"title": "  "}, {"title": "x" * 201}, {"title": 5}, {"title": "t", "description": "x" * 20001}, {"title": "t", "priority": "urgent"}, {"title": "t", "labels": ["bad label"]}):
            with self.assertRaises(ValidationError, msg=str(kw)): self.t.create_issue("WEB", kw.pop("title"), kw.pop("description", "d"), "bob", **kw)
    def test_r06_title_boundaries_and_stripping(self): self.assertEqual(self.issue("x" * 200)["title"], "x" * 200); self.assertEqual(self.issue("  padded  ")["title"], "padded")
    def test_r06_an_assignee_must_be_able_to_work_on_the_project(self):
        with self.assertRaises(ValidationError): self.t.create_issue("WEB", "x", "d", "bob", assignee="eve")
        with self.assertRaises(NotFound): self.t.create_issue("WEB", "x", "d", "bob", assignee="ghost")
        self.t.create_issue("WEB", "x", "d", "bob", assignee="root")
    def test_r06_get_issue_includes_comments(self):
        i = self.issue(); self.t.add_comment(i["key"], "first", "carol"); self.t.add_comment(i["key"], "second", "bob"); g = self.t.get_issue(i["key"], "eve"); self.assertEqual([(c["author"], c["body"], c["edited"]) for c in g["comments"]], [("carol", "first", None), ("bob", "second", None)]); self.assertEqual(sorted(g["comments"][0]), ["author", "body", "created", "edited", "id"])
    def test_r06_unknown_issue_and_malformed_keys(self):
        for k in ("WEB-99", "NOPE-1", "web-1", "WEB", "", None, "WEB-0"):
            with self.assertRaises(NotFound): self.t.get_issue(k, "root")
    def test_r06_update_returns_the_dict_and_only_touches_updated_on_real_changes(self):
        i = self.issue(); self.clock.ts = "2024-03-02T10:00:00"; u = self.t.update_issue(i["key"], "bob", title=i["title"], priority="medium"); self.assertEqual(u["updated"], "2024-03-01T09:00:00")
        u = self.t.update_issue(i["key"], "bob", title="New"); self.assertEqual((u["title"], u["updated"]), ("New", "2024-03-02T10:00:00"))
    def test_r06_update_assignee_and_unassign(self):
        i = self.issue(); self.assertEqual(self.t.update_issue(i["key"], "bob", assignee="carol")["assignee"], "carol"); self.assertIsNone(self.t.update_issue(i["key"], "bob", assignee=None)["assignee"])
    def test_r06_update_rejects_unknown_fields_and_bad_values(self):
        i = self.issue()
        for kw in ({"status": "closed"}, {"labels": []}, {"title": ""}, {"priority": "huge"}, {"description": 5}, {"assignee": "ghost"}):
            with self.assertRaises((ValidationError, NotFound)): self.t.update_issue(i["key"], "bob", **kw)
        self.assertEqual(self.t.get_issue(i["key"], "bob")["title"], "Broken login")
    def test_r06_the_workflow(self):
        i = self.issue(); k = i["key"]; T = self.t.transition
        self.assertEqual(T(k, "in_progress", "bob")["status"], "in_progress"); self.assertEqual(T(k, "open", "bob")["status"], "open"); self.assertEqual(T(k, "closed", "bob")["status"], "closed"); self.assertEqual(T(k, "open", "bob")["status"], "open")
    def test_r06_invalid_transitions(self):
        i = self.issue(); k = i["key"]
        with self.assertRaises(ValidationError): self.t.transition(k, "open", "bob")
        with self.assertRaises(ValidationError): self.t.transition(k, "done", "bob")
        self.t.transition(k, "closed", "bob")
        for s in ("in_progress", "resolved", "closed"):
            with self.assertRaises(ValidationError): self.t.transition(k, s, "bob")
    def test_r06_resolving_needs_a_resolution_which_is_cleared_on_reopen(self):
        i = self.issue(); k = i["key"]
        for r in (None, "", "   "):
            with self.assertRaises(ValidationError): self.t.transition(k, "resolved", "bob", r)
        r = self.t.transition(k, "resolved", "bob", "  fixed it  "); self.assertEqual((r["status"], r["resolution"]), ("resolved", "fixed it")); self.assertEqual(self.t.transition(k, "closed", "bob")["resolution"], "fixed it"); r = self.t.transition(k, "open", "bob"); self.assertEqual((r["status"], r["resolution"], r["closed"]), ("open", None, None))
    def test_r06_closing_sets_and_leaving_clears_closed(self):
        i = self.issue(); self.clock.ts = "2024-03-05T12:00:00"; c = self.t.transition(i["key"], "closed", "bob"); self.assertEqual((c["closed"], c["updated"]), ("2024-03-05T12:00:00", "2024-03-05T12:00:00")); self.assertIsNone(self.t.transition(i["key"], "open", "bob")["closed"])
    def test_r06_concurrent_creation_keeps_numbers_unique(self):
        import threading; errs, keys, lock = [], [], threading.Lock()
        def work(n):
            tr = self.open()
            try:
                for _ in range(10):
                    k = tr.create_issue("WEB", f"t{n}", "d", "bob")["key"]
                    with lock: keys.append(k)
            except Exception as e: errs.append(e)
            finally: tr.close()
        ts = [threading.Thread(target=work, args=(n,)) for n in range(6)]; [t.start() for t in ts]; [t.join(120) for t in ts]
        self.assertEqual(errs, []); self.assertEqual(sorted(keys, key=lambda k: int(k.split("-")[1])), [f"WEB-{i}" for i in range(1, 61)])

class TestCommentsAndLabels(Base):
    def setUp(self): super().setUp(); self.i = self.issue("T", "bob"); self.k = self.i["key"]
    def test_r07_add_comment(self):
        self.clock.ts = "2024-03-02T08:00:00"; c = self.t.add_comment(self.k, "  hello  ", "carol"); self.assertEqual((c["author"], c["body"], c["created"], c["edited"]), ("carol", "hello", "2024-03-02T08:00:00", None)); self.assertEqual(self.t.get_issue(self.k, "bob")["updated"], "2024-03-02T08:00:00")
    def test_r07_comment_validation(self):
        for b in ("", "  ", "x" * 20001, None, 5):
            with self.assertRaises(ValidationError): self.t.add_comment(self.k, b, "carol")
        self.t.add_comment(self.k, "x" * 20000, "carol")
    def test_r07_edit_own_comment_only(self):
        c = self.t.add_comment(self.k, "old", "carol"); self.clock.ts = "2024-03-03T00:00:00"; self.assertEqual(self.t.edit_comment(c["id"], "new", "carol"), {"id": c["id"], "body": "new"}); got = self.t.get_issue(self.k, "bob")["comments"][0]; self.assertEqual((got["body"], got["edited"]), ("new", "2024-03-03T00:00:00"))
        for who in ("alice", "bob", "root"):
            with self.assertRaises(PermissionDenied): self.t.edit_comment(c["id"], "hijack", who)
    def test_r07_delete_rights(self):
        c1 = self.t.add_comment(self.k, "a", "carol"); c2 = self.t.add_comment(self.k, "b", "carol"); c3 = self.t.add_comment(self.k, "c", "bob")
        with self.assertRaises(PermissionDenied): self.t.delete_comment(c3["id"], "carol")
        self.t.delete_comment(c1["id"], "carol"); self.t.delete_comment(c2["id"], "alice"); self.t.delete_comment(c3["id"], "root"); self.assertEqual(self.t.get_issue(self.k, "bob")["comments"], [])
    def test_r07_a_developer_may_delete_an_own_comment(self): c = self.t.add_comment(self.k, "x", "bob"); self.t.delete_comment(c["id"], "bob")
    def test_r07_unknown_comment_and_invisible_comment(self):
        with self.assertRaises(NotFound): self.t.edit_comment(999, "x", "carol")
        s = self.issue("S", "alice", "SEC"); c = self.t.add_comment(s["key"], "x", "alice")
        with self.assertRaises(NotFound): self.t.delete_comment(c["id"], "eve")
    def test_r07_a_removed_comment_does_not_come_back(self): c = self.t.add_comment(self.k, "x", "carol"); self.t.delete_comment(c["id"], "alice"); self.t.close(); self.t = self.open(); self.assertEqual(self.t.get_issue(self.k, "bob")["comments"], [])
    def test_r07_comment_ids_are_global_and_increasing(self): a = self.t.add_comment(self.k, "x", "carol"); b = self.t.add_comment(self.issue("U", "bob")["key"], "y", "carol"); self.assertLess(a["id"], b["id"])
    def test_r07_reporters_comment_and_viewers_do_not(self):
        self.t.add_comment(self.k, "ok", "carol")
        with self.assertRaises(PermissionDenied): self.t.add_comment(self.k, "no", "eve")
    def test_r08_labels_are_normalised_and_added_once(self):
        self.assertEqual(self.t.add_label(self.k, " Bug ", "bob")["labels"], ["bug"]); u = self.t.get_issue(self.k, "bob")["updated"]; self.clock.ts = "2024-03-09T00:00:00"; self.assertEqual(self.t.add_label(self.k, "BUG", "bob")["labels"], ["bug"]); self.assertEqual(self.t.get_issue(self.k, "bob")["updated"], u)
    def test_r08_labels_are_sorted(self): [self.t.add_label(self.k, l, "bob") for l in ("zeta", "alpha", "mid")]; self.assertEqual(self.t.get_issue(self.k, "bob")["labels"], ["alpha", "mid", "zeta"])
    def test_r08_invalid_labels(self):
        for bad in ("", "-x", "a b", "a_b", "x" * 31, "ä", 5, None):
            with self.assertRaises(ValidationError, msg=repr(bad)): self.t.add_label(self.k, bad, "bob")
        self.t.add_label(self.k, "x" * 30, "bob")
    def test_r08_at_most_ten_labels(self):
        for n in range(10): self.t.add_label(self.k, f"l{n}", "bob")
        with self.assertRaises(ValidationError): self.t.add_label(self.k, "eleven", "bob")
        self.assertEqual(len(self.t.add_label(self.k, "l3", "bob")["labels"]), 10)
    def test_r08_remove_label(self):
        self.t.add_label(self.k, "bug", "bob"); self.assertEqual(self.t.remove_label(self.k, "BUG", "bob")["labels"], [])
        with self.assertRaises(NotFound): self.t.remove_label(self.k, "bug", "bob")
    def test_r08_label_rights(self):
        for who in ("carol", "eve", "dave"):
            with self.assertRaises(PermissionDenied): self.t.add_label(self.k, "x", who)
        self.t.add_label(self.k, "x", "alice"); self.t.add_label(self.k, "y", "root")
        with self.assertRaises(PermissionDenied): self.t.remove_label(self.k, "x", "carol")
    def test_r08_labels_touch_updated_when_they_change(self): self.clock.ts = "2024-03-04T00:00:00"; self.t.add_label(self.k, "x", "bob"); self.assertEqual(self.t.get_issue(self.k, "bob")["updated"], "2024-03-04T00:00:00")
