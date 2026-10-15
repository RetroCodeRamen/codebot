from support import Base
from tracker import PermissionDenied, NotFound, ValidationError, AuthError

class TestSearch(Base):
    def setUp(self):
        super().setUp()
        self.clock.ts = "2024-03-01T09:00:00"; self.a = self.issue("Login page broken", "bob", priority="high", assignee="carol", labels=["bug", "ui"], description="the Login form crashes")
        self.clock.ts = "2024-03-02T09:00:00"; self.b = self.issue("Add dark mode", "alice", priority="low", labels=["feature", "ui"])
        self.clock.ts = "2024-03-03T09:00:00"; self.c = self.issue("Crash on save", "bob", priority="critical", assignee="bob", labels=["bug"]); self.s = self.issue("Rotate keys", "alice", "SEC", priority="medium")
        self.clock.ts = "2024-03-04T09:00:00"; self.t.transition(self.c["key"], "in_progress", "bob"); self.t.transition(self.b["key"], "resolved", "alice", "done")
    def keys(self, q="", actor="root", **kw): return [i["key"] for i in self.t.search(q, actor, **kw)["items"]]
    def test_r09_envelope(self):
        r = self.t.search("", "root"); self.assertEqual((r["total"], r["limit"], r["offset"], len(r["items"])), (4, 20, 0, 4)); self.assertNotIn("comments", r["items"][0]); self.assertEqual(sorted(r["items"][0]), sorted(k for k in self.a if k != "comments"))
    def test_r09_default_sort_is_updated_descending(self): self.assertEqual(self.keys("project:WEB"), ["WEB-3", "WEB-2", "WEB-1"]); self.assertEqual(self.keys()[-1], "WEB-1")
    def test_r09_project_filter(self): self.assertEqual(sorted(self.keys("project:WEB")), ["WEB-1", "WEB-2", "WEB-3"]); self.assertEqual(self.keys("project:web"), self.keys("project:WEB")); self.assertEqual(self.keys("project:SEC"), ["SEC-1"])
    def test_r09_status_filter_with_alternatives(self): self.assertEqual(self.keys("status:open", sort="number"), ["WEB-1", "SEC-1"]); self.assertEqual(sorted(self.keys("status:in_progress,resolved")), ["WEB-2", "WEB-3"]); self.assertEqual(self.keys("status:closed"), [])
    def test_r09_priority_filter(self): self.assertEqual(sorted(self.keys("priority:high,critical")), ["WEB-1", "WEB-3"]); self.assertEqual(self.keys("priority:low"), ["WEB-2"])
    def test_r09_assignee_filter(self): self.assertEqual(self.keys("assignee:carol"), ["WEB-1"]); self.assertEqual(self.keys("assignee:CAROL"), ["WEB-1"]); self.assertEqual(sorted(self.keys("assignee:none")), ["SEC-1", "WEB-2"]); self.assertEqual(self.keys("assignee:nobody"), [])
    def test_r09_reporter_filter(self): self.assertEqual(sorted(self.keys("reporter:bob")), ["WEB-1", "WEB-3"]); self.assertEqual(sorted(self.keys("reporter:alice")), ["SEC-1", "WEB-2"])
    def test_r09_label_filters_are_all_required(self): self.assertEqual(sorted(self.keys("label:ui")), ["WEB-1", "WEB-2"]); self.assertEqual(self.keys("label:ui label:bug"), ["WEB-1"]); self.assertEqual(self.keys("label:UI label:feature"), ["WEB-2"]); self.assertEqual(self.keys("label:none-such"), [])
    def test_r09_words_search_title_and_description_ignoring_case(self): self.assertEqual(self.keys("login"), ["WEB-1"]); self.assertEqual(self.keys("LOGIN crashes"), ["WEB-1"]); self.assertEqual(self.keys("crash"), ["WEB-3", "WEB-1"]); self.assertEqual(self.keys("dark mode"), ["WEB-2"]); self.assertEqual(self.keys("login dark"), [])
    def test_r09_like_wildcards_in_words_are_literal(self): self.assertEqual(self.keys("%"), []); self.assertEqual(self.keys("_ogin"), []); self.issue("100% done", "bob"); self.assertEqual(len(self.keys("100%")), 1)
    def test_r09_filters_and_words_combine(self): self.assertEqual(self.keys("project:WEB status:open label:bug crash"), ["WEB-1"]); self.assertEqual(self.keys("priority:critical label:bug"), ["WEB-3"])
    def test_r09_visibility(self): self.assertEqual(self.keys("", "eve"), ["WEB-3", "WEB-2", "WEB-1"]); self.assertEqual(self.keys("", "alice")[:1], ["WEB-3"]); self.assertEqual(len(self.keys("", "alice")), 4); self.assertEqual(self.keys("project:SEC", "eve"), []); self.assertEqual(self.t.search("", "eve")["total"], 3)
    def test_r09_sorting(self):
        self.assertEqual(self.keys("project:WEB", sort="number"), ["WEB-1", "WEB-2", "WEB-3"]); self.assertEqual(self.keys("project:WEB", sort="-number"), ["WEB-3", "WEB-2", "WEB-1"]); self.assertEqual(self.keys("project:WEB", sort="created"), ["WEB-1", "WEB-2", "WEB-3"]); self.assertEqual(self.keys("project:WEB", sort="-created"), ["WEB-3", "WEB-2", "WEB-1"])
        self.assertEqual(self.keys("project:WEB", sort="-priority"), ["WEB-3", "WEB-1", "WEB-2"]); self.assertEqual(self.keys("project:WEB", sort="priority"), ["WEB-2", "WEB-1", "WEB-3"]); self.assertEqual(self.keys("project:WEB", sort="updated"), self.keys("project:WEB", sort="-updated")[::-1])
    def test_r09_ties_are_broken_by_the_internal_id(self): self.assertEqual(self.keys("project:WEB status:in_progress,resolved", sort="-updated"), ["WEB-3", "WEB-2"]); self.assertEqual(self.keys("project:WEB status:in_progress,resolved", sort="updated"), ["WEB-2", "WEB-3"])
    def test_r09_paging(self):
        for n in range(30): self.issue(f"bulk {n}", "bob")
        r = self.t.search("bulk", "root", limit=10, offset=5, sort="number"); self.assertEqual((r["total"], len(r["items"]), r["limit"], r["offset"]), (30, 10, 10, 5)); self.assertEqual([i["title"] for i in r["items"]][:2], ["bulk 5", "bulk 6"]); self.assertEqual(self.t.search("bulk", "root", limit=10, offset=100)["items"], [])
        seen = []
        for off in range(0, 40, 7): seen += [i["key"] for i in self.t.search("", "root", limit=7, offset=off, sort="number")["items"]]
        self.assertEqual(len(seen), len(set(seen)))
    def test_r09_validation(self):
        for kw in ({"limit": 0}, {"limit": 101}, {"limit": "5"}, {"limit": True}, {"offset": -1}, {"offset": 1.5}, {"sort": "title"}, {"sort": ""}):
            with self.assertRaises(ValidationError, msg=str(kw)): self.t.search("", "root", **kw)
        for q in ("color:red", "status:", "label:"):
            with self.assertRaises(ValidationError): self.t.search(q, "root")
    def test_r09_limit_100_is_allowed(self): self.assertEqual(self.t.search("", "root", limit=100)["limit"], 100)
    def test_r09_stats(self):
        s = self.t.project_stats("WEB", "eve"); self.assertEqual(s, {"project": "WEB", "total": 3, "by_status": {"in_progress": 1, "open": 1, "resolved": 1}, "by_priority": {"critical": 1, "high": 1, "low": 1}, "by_assignee": {"-": 1, "bob": 1, "carol": 1}, "by_label": {"bug": 2, "feature": 1, "ui": 2}}); self.assertEqual(list(s["by_assignee"]), ["-", "bob", "carol"])
    def test_r09_stats_of_an_empty_project_and_hidden_projects(self):
        self.t.create_project("NEW", "n", "root", public=True); self.assertEqual(self.t.project_stats("NEW", "bob"), {"project": "NEW", "total": 0, "by_status": {}, "by_priority": {}, "by_assignee": {}, "by_label": {}})
        with self.assertRaises(NotFound): self.t.project_stats("SEC", "eve")

class TestAudit(Base):
    def last(self, **kw): return self.t.audit_entries("root", **kw)[-1]
    def test_r10_entry_shape_and_sequence(self):
        es = self.t.audit_entries("root"); self.assertTrue(all(sorted(e) == sorted(["seq", "ts", "actor", "action", "entity", "entity_id", "outcome", "changes", "reason"]) for e in es)); self.assertEqual([e["seq"] for e in es], list(range(1, len(es) + 1)))
    def test_r10_only_admins_read_it(self):
        for who in ("alice", "bob", "dave"):
            with self.assertRaises(PermissionDenied): self.t.audit_entries(who)
    def test_r10_setup_actions(self):
        es = self.t.audit_entries("root"); self.assertEqual(es[0]["action"], "user.create"); self.assertEqual((es[0]["actor"], es[0]["entity"], es[0]["entity_id"], es[0]["changes"]), (None, "user", "root", {"name": [None, "root"], "role": [None, "admin"]})); self.assertEqual(es[1]["actor"], "root")
        self.assertIn(("project.create", "project", "WEB"), [(e["action"], e["entity"], e["entity_id"]) for e in es]); e = [x for x in es if x["action"] == "project.member_add"][0]; self.assertEqual(e["changes"], {"member": [None, "alice"], "role": [None, "manager"]})
    def test_r10_issue_create(self):
        i = self.issue("T", "bob", priority="high", assignee="carol", labels=["x"]); e = self.last(); self.assertEqual((e["action"], e["entity"], e["entity_id"], e["actor"], e["outcome"], e["reason"]), ("issue.create", "issue", i["key"], "bob", "ok", None)); self.assertEqual(e["changes"], {"title": [None, "T"], "priority": [None, "high"], "assignee": [None, "carol"], "labels": [[], ["x"]]})
    def test_r10_update_and_assign_are_separate_entries(self):
        i = self.issue(); self.t.update_issue(i["key"], "bob", title="New", priority="low", assignee="carol"); es = self.t.audit_entries("root", entity_id=i["key"]); self.assertEqual([e["action"] for e in es], ["issue.create", "issue.update", "issue.assign"])
        self.assertEqual(es[1]["changes"], {"title": ["Broken login", "New"], "priority": ["medium", "low"]}); self.assertEqual(es[2]["changes"], {"assignee": [None, "carol"]})
    def test_r10_no_entry_for_unchanged_updates(self): i = self.issue(); n = len(self.t.audit_entries("root")); self.t.update_issue(i["key"], "bob", title=i["title"]); self.t.add_label(i["key"], "x", "bob"); self.t.add_label(i["key"], "x", "bob"); self.assertEqual(len(self.t.audit_entries("root")), n + 1)
    def test_r10_transitions(self):
        i = self.issue(); self.t.transition(i["key"], "resolved", "bob", "fixed"); self.assertEqual(self.last()["changes"], {"status": ["open", "resolved"], "resolution": [None, "fixed"]}); self.t.transition(i["key"], "closed", "bob"); self.assertEqual(self.last()["changes"], {"status": ["resolved", "closed"]})
        self.t.transition(i["key"], "open", "bob"); self.assertEqual(self.last()["changes"], {"status": ["closed", "open"], "resolution": ["fixed", None]})
    def test_r10_labels(self): i = self.issue(); self.t.add_label(i["key"], "b", "bob"); self.t.add_label(i["key"], "a", "bob"); e = self.last(); self.assertEqual((e["action"], e["changes"]), ("label.add", {"labels": [["b"], ["a", "b"]]})); self.t.remove_label(i["key"], "b", "bob"); e = self.last(); self.assertEqual((e["action"], e["changes"]), ("label.remove", {"labels": [["a", "b"], ["a"]]}))
    def test_r10_comments(self):
        i = self.issue(); c = self.t.add_comment(i["key"], "x", "carol"); e = self.last(); self.assertEqual((e["action"], e["entity"], e["entity_id"], e["changes"]), ("comment.add", "issue", i["key"], {"comment_id": [None, c["id"]]})); self.t.edit_comment(c["id"], "y", "carol"); self.assertEqual(self.last()["action"], "comment.edit"); self.t.delete_comment(c["id"], "alice"); e = self.last(); self.assertEqual((e["action"], e["entity_id"], e["actor"]), ("comment.delete", i["key"], "alice"))
    def test_r10_member_and_project_changes(self):
        self.t.add_member("WEB", "eve", "reporter", "alice"); n = len(self.t.audit_entries("root")); self.t.add_member("WEB", "eve", "reporter", "alice"); self.assertEqual(len(self.t.audit_entries("root")), n); self.t.add_member("WEB", "eve", "developer", "alice"); self.assertEqual(self.last()["changes"], {"member": [None, "eve"], "role": ["reporter", "developer"]})
        self.t.remove_member("WEB", "eve", "alice"); self.assertEqual(self.last()["action"], "project.member_remove"); self.t.archive_project("WEB", "root"); self.assertEqual((self.last()["action"], self.last()["changes"]), ("project.archive", {"archived": [False, True]}))
    def test_r10_user_changes(self): self.t.set_active("bob", False, "root"); e = self.last(); self.assertEqual((e["action"], e["entity_id"], e["changes"]), ("user.active", "bob", {"active": [True, False]}))
    def test_r10_logins(self):
        self.t.login("bob", "password1"); e = self.last(); self.assertEqual((e["action"], e["entity"], e["entity_id"], e["actor"], e["outcome"], e["changes"]), ("auth.login", "user", "bob", "bob", "ok", {}))
        with self.assertRaises(AuthError): self.t.login("bob", "wrong-pass")
        e = self.last(); self.assertEqual((e["action"], e["actor"], e["outcome"], e["reason"]), ("auth.login", "bob", "denied", "invalid credentials"))
        with self.assertRaises(AuthError): self.t.login("ghost", "password1")
        self.assertEqual((self.last()["actor"], self.last()["outcome"]), ("ghost", "denied"))
    def test_r10_denied_attempts(self):
        i = self.issue(); cases = [(lambda: self.t.transition(i["key"], "closed", "carol"), "issue.transition", i["key"]), (lambda: self.t.update_issue(i["key"], "carol", priority="high"), "issue.update", i["key"]), (lambda: self.t.add_label(i["key"], "x", "carol"), "label.add", i["key"]),
                                   (lambda: self.t.create_issue("WEB", "x", "d", "eve"), "issue.create", None), (lambda: self.t.add_comment(i["key"], "x", "eve"), "comment.add", i["key"]), (lambda: self.t.create_project("ZZ", "z", "alice"), "project.create", "ZZ"), (lambda: self.t.import_project({}, "alice"), "project.import", None)]
        for call, action, eid in cases:
            n = len(self.t.audit_entries("root"))
            with self.assertRaises(PermissionDenied): call()
            es = self.t.audit_entries("root"); self.assertEqual(len(es), n + 1, action); e = es[-1]; self.assertEqual((e["action"], e["outcome"], e["entity_id"], e["changes"]), (action, "denied", eid, {})); self.assertTrue(e["reason"]); self.assertIn(e["actor"], ("carol", "eve", "alice"))
    def test_r10_validation_errors_and_hidden_objects_write_nothing(self):
        i = self.issue(); n = len(self.t.audit_entries("root"))
        for call, exc in ((lambda: self.t.create_issue("WEB", "", "d", "bob"), ValidationError), (lambda: self.t.transition(i["key"], "open", "bob"), ValidationError), (lambda: self.t.get_issue("SEC-1", "eve"), NotFound), (lambda: self.t.add_comment("WEB-99", "x", "bob"), NotFound)):
            with self.assertRaises(exc): call()
        self.assertEqual(len(self.t.audit_entries("root")), n)
    def test_r10_filters(self):
        i = self.issue(); self.t.add_comment(i["key"], "x", "carol")
        A = lambda **kw: self.t.audit_entries("root", **kw)
        self.assertEqual([e["action"] for e in A(entity="issue", entity_id=i["key"])], ["issue.create", "comment.add"]); self.assertEqual(len(A(action="issue.create")), 1); self.assertEqual([e["action"] for e in A(who="carol")], ["comment.add"]); self.assertEqual(A(outcome="denied"), [])
        self.assertEqual(len(A(limit=2)), 2); self.assertEqual(A(limit=2)[-1]["action"], "comment.add"); self.clock.ts = "2024-04-01T00:00:00"; self.issue("later"); self.assertEqual([e["action"] for e in A(since="2024-04-01T00:00:00")], ["issue.create"]); self.assertEqual(A(entity_id=5), A(entity_id="5"))
    def test_r10_timestamps_come_from_the_clock(self): self.clock.ts = "2024-05-05T05:05:05"; self.issue(); self.assertEqual(self.last()["ts"], "2024-05-05T05:05:05")
    def test_r10_entity_id_is_always_a_string_or_none(self):
        for e in self.t.audit_entries("root"): self.assertTrue(e["entity_id"] is None or isinstance(e["entity_id"], str))
    def test_r10_failed_operations_roll_back_their_audit_entries(self):
        n = len(self.t.audit_entries("root"))
        with self.assertRaises(ValidationError): self.t.create_issue("WEB", "ok", "d", "bob", priority="nope")
        self.assertEqual(len(self.t.audit_entries("root")), n); self.assertEqual([e["seq"] for e in self.t.audit_entries("root")], list(range(1, n + 1)))
    def test_r10_the_log_survives_reopening(self): self.issue(); n = len(self.t.audit_entries("root")); self.t.close(); self.t = self.open(); self.assertEqual(len(self.t.audit_entries("root")), n)
