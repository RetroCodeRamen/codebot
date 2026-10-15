import http.client, json, socket, threading
from support import Base
from tracker import Tracker
from tracker.api import make_server

class ApiBase(Base):
    def setUp(self):
        super().setUp(); self.server = make_server(self.t).start(); self.addCleanup(self.server.stop); self.tok = {n: self.call("POST", "/login", {"name": n, "password": "password1"}, None)[1]["token"] for n in ("root", "alice", "bob", "carol", "eve", "dave")}
    def call(self, method, path, body=None, who="root", raw=None, headers=None, port=None):
        h = dict(headers or {})
        if who: h["Authorization"] = "Bearer " + (self.tok[who] if who in getattr(self, "tok", {}) else who)
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        c = http.client.HTTPConnection("127.0.0.1", port or self.server.port, timeout=60)
        try: c.request(method, path, body=data, headers=h); r = c.getresponse(); text = r.read().decode(); hd = {k.lower(): v for k, v in r.getheaders()}
        finally: c.close()
        return r.status, (json.loads(text) if text else None), hd

class TestApiBasics(ApiBase):
    def test_r12_lifecycle(self):
        self.assertGreater(self.server.port, 0); s, b, h = self.call("GET", "/health", who=None); self.assertEqual((s, b), (200, {"status": "ok"})); self.assertEqual(h["content-type"], "application/json")
    def test_r12_stop_releases_the_port(self):
        port = self.server.port; self.server.stop(); s = socket.socket(); s.settimeout(2)
        try: self.assertNotEqual(s.connect_ex(("127.0.0.1", port)), 0)
        finally: s.close()
    def test_r12_context_manager_and_independent_servers(self):
        with make_server(self.t) as other: self.assertNotEqual(other.port, self.server.port); self.assertEqual(self.call("GET", "/health", who=None, port=other.port)[0], 200)
    def test_r12_login(self):
        s, b, h = self.call("POST", "/login", {"name": "bob", "password": "password1"}, None); self.assertEqual(s, 200); self.assertGreaterEqual(len(b["token"]), 32); s, b, h = self.call("POST", "/login", {"name": "bob", "password": "bad"}, None); self.assertEqual((s, b), (401, {"error": "invalid credentials"}))
    def test_r12_authentication_is_required_everywhere_except_health_and_login(self):
        for m, p in (("GET", "/me"), ("GET", "/projects"), ("GET", "/issues"), ("POST", "/logout"), ("GET", "/audit"), ("GET", "/projects/WEB")):
            s, b, h = self.call(m, p, who=None); self.assertEqual(s, 401, p); self.assertEqual(h["www-authenticate"], "Bearer"); self.assertEqual(b, {"error": "unauthorized"})
    def test_r12_bad_tokens_and_malformed_headers(self):
        self.assertEqual(self.call("GET", "/me", who="nope")[0], 401)
        for hdr in ("Basic abc", "Bearer", "bearer " + self.tok["bob"], self.tok["bob"]): self.assertEqual(self.call("GET", "/me", who=None, headers={"Authorization": hdr})[0], 401, hdr)
    def test_r12_me_and_logout(self):
        self.assertEqual(self.call("GET", "/me", who="bob")[1]["name"], "bob"); s, b, h = self.call("POST", "/logout", who="bob"); self.assertEqual((s, b), (204, None)); self.assertEqual(self.call("GET", "/me", who="bob")[0], 401)
    def test_r12_a_disabled_user_loses_access(self): self.t.set_active("carol", False, "root"); self.assertEqual(self.call("GET", "/me", who="carol")[0], 401)
    def test_r12_unknown_routes_and_method_errors(self):
        self.assertEqual(self.call("GET", "/nothing")[:2], (404, {"error": "not found"})); self.assertEqual(self.call("GET", "/nothing", who=None)[0], 404)
        s, b, h = self.call("DELETE", "/projects"); self.assertEqual((s, h["allow"]), (405, "GET, POST")); s, b, h = self.call("PUT", "/issues/WEB-1"); self.assertEqual((s, h["allow"]), (405, "GET, PATCH")); s, b, h = self.call("GET", "/issues/WEB-1/transition"); self.assertEqual((s, h["allow"]), (405, "POST"))
        self.assertEqual(self.call("DELETE", "/projects", who=None)[0], 405)
    def test_r12_json_body_errors(self):
        self.assertEqual(self.call("POST", "/projects", raw=b"{nope")[:2], (400, {"error": "invalid JSON"})); self.assertEqual(self.call("POST", "/projects", raw=b"[1]")[:2], (400, {"error": "body must be a JSON object"})); self.assertEqual(self.call("PATCH", "/issues/WEB-1", raw=b"5")[0], 400)
    def test_r12_oversized_body(self):
        s, b, h = self.call("POST", "/projects", raw=b"x" * 1_200_000); self.assertEqual((s, b), (413, {"error": "request too large"})); self.assertEqual(self.call("GET", "/health", who=None)[0], 200)
    def test_r12_check_order_is_route_method_auth_body(self):
        self.assertEqual(self.call("POST", "/projects", raw=b"{bad", who=None)[0], 401); self.assertEqual(self.call("POST", "/projects", raw=b"{bad", who="alice")[0], 400)
    def test_r12_keep_alive_and_no_logs(self):
        import contextlib, io; buf = io.StringIO(); c = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=30)
        with contextlib.redirect_stderr(buf):
            for _ in range(3): c.request("GET", "/me", headers={"Authorization": "Bearer " + self.tok["bob"]}); r = c.getresponse(); r.read(); self.assertEqual(r.status, 200)
        c.close(); self.assertEqual(buf.getvalue(), "")

class TestApiProjects(ApiBase):
    def test_r12_create_and_read_projects(self):
        s, b, h = self.call("POST", "/projects", {"key": "NEW", "name": "New one", "public": True}); self.assertEqual((s, h["location"]), (201, "/projects/NEW")); self.assertEqual(b["key"], "NEW"); self.assertEqual(self.call("GET", "/projects/NEW", who="eve")[1]["name"], "New one")
    def test_r12_project_list_is_an_envelope_and_filtered_by_visibility(self):
        self.assertEqual([p["key"] for p in self.call("GET", "/projects", who="eve")[1]["items"]], ["WEB"]); self.assertEqual([p["key"] for p in self.call("GET", "/projects", who="alice")[1]["items"]], ["SEC", "WEB"])
    def test_r12_status_codes_for_projects(self):
        self.assertEqual(self.call("POST", "/projects", {"key": "WEB", "name": "dup"})[0], 409); self.assertEqual(self.call("POST", "/projects", {"key": "bad", "name": "x"})[0], 422); self.assertEqual(self.call("POST", "/projects", {"key": "ZZ", "name": "x"}, who="alice")[0], 403)
        self.assertEqual(self.call("GET", "/projects/SEC", who="eve")[0], 404); self.assertEqual(self.call("GET", "/projects/NOPE")[0], 404); self.assertEqual(self.call("GET", "/projects/web")[0], 404)
    def test_r12_members(self):
        self.assertEqual(self.call("POST", "/projects/WEB/members", {"user": "eve", "role": "reporter"}, who="alice")[1], {"user": "eve", "role": "reporter"}); self.assertEqual(self.call("POST", "/projects/WEB/members", {"user": "eve", "role": "boss"}, who="alice")[0], 422); self.assertEqual(self.call("POST", "/projects/WEB/members", {"user": "eve", "role": "reporter"}, who="bob")[0], 403)
        self.assertEqual(self.call("DELETE", "/projects/WEB/members/eve", who="alice")[:2], (204, None)); self.assertEqual(self.call("DELETE", "/projects/WEB/members/eve", who="alice")[0], 404)
    def test_r12_stats_and_export(self):
        self.call("POST", "/projects/WEB/issues", {"title": "A", "labels": ["x"]}, who="bob"); s, b, h = self.call("GET", "/projects/WEB/stats", who="eve"); self.assertEqual((s, b["total"], b["by_label"]), (200, 1, {"x": 1})); self.assertEqual(self.call("GET", "/projects/WEB/export", who="alice")[1]["format"], "tracker-export"); self.assertEqual(self.call("GET", "/projects/WEB/export", who="bob")[0], 403)

class TestApiIssues(ApiBase):
    def create(self, who="bob", **kw): return self.call("POST", "/projects/WEB/issues", {"title": "Broken login", **kw}, who=who)
    def test_r12_create_issue(self):
        s, b, h = self.create(description="d", priority="high", assignee="carol", labels=["bug"]); self.assertEqual((s, h["location"], b["key"], b["priority"], b["assignee"], b["labels"], b["reporter"]), (201, "/issues/WEB-1", "WEB-1", "high", "carol", ["bug"], "bob"))
    def test_r12_create_errors(self): self.assertEqual(self.create(who="eve")[0], 403); self.assertEqual(self.create(title="")[0], 422); self.assertEqual(self.call("POST", "/projects/SEC/issues", {"title": "x"}, who="eve")[0], 404); self.assertEqual(self.call("POST", "/projects/WEB/issues", {"title": "x"}, who=None)[0], 401)
    def test_r12_get_issue(self):
        self.create(); self.call("POST", "/issues/WEB-1/comments", {"body": "hello"}, who="carol"); s, b, h = self.call("GET", "/issues/WEB-1", who="eve"); self.assertEqual((s, b["comments"][0]["body"]), (200, "hello")); self.assertEqual(self.call("GET", "/issues/WEB-9")[0], 404); self.assertEqual(self.call("GET", "/issues/SEC-1", who="eve")[0], 404)
    def test_r12_patch_issue(self):
        self.create(); self.assertEqual(self.call("PATCH", "/issues/WEB-1", {"title": "New", "assignee": "carol"}, who="bob")[1]["assignee"], "carol"); self.assertEqual(self.call("PATCH", "/issues/WEB-1", {"priority": "high"}, who="carol")[0], 403); self.assertEqual(self.call("PATCH", "/issues/WEB-1", {"status": "closed"}, who="bob")[0], 422); self.assertIsNone(self.call("PATCH", "/issues/WEB-1", {"assignee": None}, who="bob")[1]["assignee"])
    def test_r12_transition(self):
        self.create(); self.assertEqual(self.call("POST", "/issues/WEB-1/transition", {"status": "resolved", "resolution": "fixed"}, who="bob")[1]["status"], "resolved"); self.assertEqual(self.call("POST", "/issues/WEB-1/transition", {"status": "open"}, who="carol")[0], 403); self.assertEqual(self.call("POST", "/issues/WEB-1/transition", {"status": "in_progress"}, who="bob")[0], 422)
    def test_r12_comments(self):
        self.create(); s, b, h = self.call("POST", "/issues/WEB-1/comments", {"body": "x"}, who="carol"); self.assertEqual((s, b["author"]), (201, "carol")); cid = b["id"]
        self.assertEqual(self.call("PATCH", f"/comments/{cid}", {"body": "y"}, who="carol")[1], {"id": cid, "body": "y"}); self.assertEqual(self.call("PATCH", f"/comments/{cid}", {"body": "z"}, who="alice")[0], 403); self.assertEqual(self.call("DELETE", f"/comments/{cid}", who="alice")[:2], (204, None)); self.assertEqual(self.call("DELETE", f"/comments/{cid}", who="alice")[0], 404)
    def test_r12_labels(self):
        self.create(); self.assertEqual(self.call("POST", "/issues/WEB-1/labels", {"label": "Bug"}, who="bob")[1]["labels"], ["bug"]); self.assertEqual(self.call("POST", "/issues/WEB-1/labels", {"label": "x"}, who="carol")[0], 403); self.assertEqual(self.call("DELETE", "/issues/WEB-1/labels/bug", who="bob")[1]["labels"], []); self.assertEqual(self.call("DELETE", "/issues/WEB-1/labels/bug", who="bob")[0], 404)
    def test_r12_search_envelope_and_filters(self):
        for t, p in (("alpha", "low"), ("beta", "high"), ("gamma", "high")): self.create(title=t, priority=p)
        s, b, h = self.call("GET", "/issues?q=priority:high&sort=number", who="eve"); self.assertEqual((s, b["total"], [i["title"] for i in b["items"]], b["limit"], b["offset"]), (200, 2, ["beta", "gamma"], 20, 0)); self.assertEqual(self.call("GET", "/issues?q=alp")[1]["total"], 1)
        s, b, h = self.call("GET", "/issues?limit=1&offset=1&sort=number"); self.assertEqual(([i["title"] for i in b["items"]], b["total"]), (["beta"], 3))
    def test_r12_search_errors(self):
        for q, want in (("limit=0", 400), ("limit=101", 400), ("limit=x", 400), ("offset=-1", 400), ("offset=1.5", 400), ("limit=", 400), ("q=color:red", 422), ("sort=title", 422)): self.assertEqual(self.call("GET", "/issues?" + q)[0], want, q)
        self.assertEqual(self.call("GET", "/issues?limit=0")[1], {"error": "invalid limit"}); self.assertEqual(self.call("GET", "/issues?offset=-1")[1], {"error": "invalid offset"})
    def test_r12_search_respects_visibility(self): self.call("POST", "/projects/SEC/issues", {"title": "secret"}, who="alice"); self.assertEqual(self.call("GET", "/issues", who="eve")[1]["total"], 0); self.assertEqual(self.call("GET", "/issues", who="alice")[1]["total"], 1)
    def test_r12_an_archived_project_is_read_only_over_http(self): self.create(); self.t.archive_project("WEB", "root"); self.assertEqual(self.call("POST", "/issues/WEB-1/comments", {"body": "x"}, who="root")[0], 403); self.assertEqual(self.call("GET", "/issues/WEB-1", who="root")[0], 200)

class TestApiAudit(ApiBase):
    def test_r12_audit_endpoint(self):
        self.call("POST", "/projects/WEB/issues", {"title": "x"}, who="bob"); self.call("POST", "/projects/WEB/issues", {"title": "x"}, who="eve")
        s, b, h = self.call("GET", "/audit?action=issue.create&limit=5"); self.assertEqual((s, [(e["actor"], e["outcome"]) for e in b["items"]], b["total"]), (200, [("bob", "ok"), ("eve", "denied")], 2)); self.assertEqual(self.call("GET", "/audit?outcome=denied&actor=eve")[1]["total"], 1)
        self.assertEqual(self.call("GET", "/audit?entity=issue&id=WEB-1")[1]["total"], 1); self.assertEqual(self.call("GET", "/audit", who="alice")[0], 403)
    def test_r12_failed_http_logins_are_audited(self): self.call("POST", "/login", {"name": "bob", "password": "nope"}, None); e = self.call("GET", "/audit?action=auth.login&outcome=denied")[1]["items"][-1]; self.assertEqual((e["actor"], e["reason"]), ("bob", "invalid credentials"))
    def test_r12_denied_http_requests_are_audited(self): self.call("POST", "/issues/WEB-1/transition", {"status": "closed"}, who="carol"); self.assertEqual(self.call("GET", "/audit")[1]["total"] >= 1, True)

class TestApiConcurrency(ApiBase):
    def test_r12_parallel_requests(self):
        results, lock = [], threading.Lock()
        def work(n):
            for j in range(5):
                s, b, h = self.call("POST", "/projects/WEB/issues", {"title": f"t{n}-{j}"}, who="bob")
                with lock: results.append((s, b["key"] if s == 201 else None))
        ts = [threading.Thread(target=work, args=(n,)) for n in range(6)]; [t.start() for t in ts]; [t.join(180) for t in ts]
        self.assertEqual([s for s, _ in results], [201] * 30); self.assertEqual(sorted(int(k.split("-")[1]) for _, k in results), list(range(1, 31)))
    def test_r12_a_server_over_a_file_database_survives_a_restart(self):
        self.call("POST", "/projects/WEB/issues", {"title": "persist"}, who="bob"); self.server.stop(); self.t.close(); self.t = self.open(); self.server = make_server(self.t).start(); self.addCleanup(self.server.stop)
        tok = self.call("POST", "/login", {"name": "bob", "password": "password1"}, None)[1]["token"]; self.tok["bob"] = tok; self.assertEqual(self.call("GET", "/issues/WEB-1", who="bob")[1]["title"], "persist")
