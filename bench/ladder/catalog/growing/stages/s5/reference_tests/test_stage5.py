import http.client, json, os, shutil, subprocess, sys, tempfile, unittest
from notes import AuthError, NoteStore, NotesError, NotFound, PermissionDenied, ValidationError, make_server

WS = os.getcwd()
HERE = os.path.dirname(os.path.abspath(__file__))

class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="g5-"); self.path = os.path.join(self.dir, "n.json"); self.now = "2024-03-01T09:00:00"; self.s = NoteStore(self.path, clock=lambda: self.now)
        for n in ("alice", "bob", "carol"): self.s.create_user(n, "password1")
    def tearDown(self): shutil.rmtree(self.dir, ignore_errors=True)

class TestUsers(Base):
    def test_r01_create_user(self): self.assertEqual(self.s.create_user("Dave.X", "password1"), {"name": "Dave.X"})
    def test_r01_names_are_unique_ignoring_case(self):
        with self.assertRaises(ValidationError): self.s.create_user("ALICE", "password1")
    def test_r01_invalid_names_and_passwords(self):
        for n in ("", "a", "x" * 31, "1abc", "a b", None, 5):
            with self.assertRaises(ValidationError, msg=repr(n)): self.s.create_user(n, "password1")
        for p in ("short", "", None, 12345678):
            with self.assertRaises(ValidationError): self.s.create_user("zed", p)
    def test_r01_passwords_are_not_stored_in_clear_text(self): raw = open(self.path, "rb").read(); self.assertNotIn(b"password1", raw)
    def test_r01_login_and_token(self): tok = self.s.login("ALICE", "password1"); self.assertGreaterEqual(len(tok), 32); self.assertEqual(self.s.user_for_token(tok), "alice"); self.assertNotEqual(tok, self.s.login("alice", "password1"))
    def test_r01_every_failure_looks_the_same(self):
        for a in (("alice", "wrongpass1"), ("nobody", "password1"), (None, "password1"), ("alice", None)):
            with self.assertRaises(AuthError) as cm: self.s.login(*a)
            self.assertEqual(str(cm.exception), "invalid credentials")
    def test_r01_tokens_expire_after_24_hours(self):
        tok = self.s.login("alice", "password1"); self.now = "2024-03-02T08:59:59"; self.assertEqual(self.s.user_for_token(tok), "alice"); self.now = "2024-03-02T09:00:00"
        with self.assertRaises(AuthError): self.s.user_for_token(tok)
    def test_r01_logout_and_bad_tokens(self):
        tok = self.s.login("alice", "password1"); self.s.logout(tok)
        for bad in (tok, "", "nope", None, 5):
            with self.assertRaises(AuthError): self.s.user_for_token(bad)
        self.s.logout(tok)
    def test_r01_error_classes(self): self.assertTrue(issubclass(AuthError, NotesError) and issubclass(PermissionDenied, NotesError))
    def test_r01_users_and_tokens_persist(self): tok = self.s.login("bob", "password1"); s2 = NoteStore(self.path, clock=lambda: self.now); self.assertEqual(s2.user_for_token(tok), "bob"); self.assertTrue(s2.login("carol", "password1"))

class TestOwnership(Base):
    def test_r02_owner_is_the_actor_or_none(self): a = self.s.add("A", actor="alice"); n = self.s.add("N"); self.assertEqual((a["owner"], n["owner"]), ("alice", None))
    def test_r02_the_actor_name_keeps_the_stored_spelling(self): self.assertEqual(self.s.add("A", actor="ALICE")["owner"], "alice")
    def test_r02_an_unknown_actor(self):
        with self.assertRaises(NotFound): self.s.add("A", actor="ghost")
        with self.assertRaises(NotFound): self.s.list(actor="ghost")
    def test_r02_without_an_actor_nothing_is_restricted(self): self.s.add("A", actor="alice"); self.assertEqual(len(self.s.list()), 1); self.assertEqual(self.s.get(1)["title"], "A"); self.s.update(1, title="B"); self.s.delete(1)
    def test_r02_users_see_only_their_own_notes(self):
        self.s.add("A1", actor="alice"); self.s.add("B1", actor="bob"); self.assertEqual([n["title"] for n in self.s.list("alice")], ["A1"]); self.assertEqual([n["title"] for n in self.s.list("bob")], ["B1"]); self.assertEqual(len(self.s.list()), 2)
    def test_r02_other_peoples_notes_do_not_exist(self):
        self.s.add("A1", actor="alice")
        for call in (lambda: self.s.get(1, "bob"), lambda: self.s.update(1, title="x", actor="bob"), lambda: self.s.delete(1, "bob")):
            with self.assertRaises(NotFound) as cm: call()
            self.assertEqual(cm.exception.args[0], 1)
        self.assertEqual(self.s.get(1)["title"], "A1")
    def test_r02_unowned_notes_belong_to_everybody(self):
        self.s.add("Shared space"); self.assertEqual([n["id"] for n in self.s.list("bob")], [1]); self.s.update(1, title="edited", actor="bob"); self.assertEqual(self.s.get(1, "carol")["title"], "edited"); self.s.delete(1, "alice"); self.assertEqual(self.s.list(), [])
    def test_r02_search_and_tags_respect_ownership(self):
        self.s.add("Milk", tags=["home"], actor="alice"); self.s.add("Milk shake", tags=["home", "fun"], actor="bob"); self.assertEqual([n["id"] for n in self.s.search("milk", actor="alice")], [1]); self.assertEqual(self.s.tags("bob"), [("fun", 1), ("home", 1)]); self.assertEqual(self.s.tags(), [("fun", 1), ("home", 2)]); self.assertEqual(self.s.search("milk", ["fun"], "alice"), [])
    def test_r02_stage_4_behaviour_without_actors_is_unchanged(self): n = self.s.add("Hello", "World", ["a"]); self.assertEqual((n["id"], n["tags"]), (1, ["a"])); self.assertEqual(self.s.update(1, tags=["b"])["tags"], ["b"]); self.assertEqual([x["id"] for x in self.s.search("hello", ["b"])], [1]); self.assertEqual(self.s.tags(), [("b", 1)])

class TestSharing(Base):
    def setUp(self): super().setUp(); self.s.add("Mine", "secret", ["t"], actor="alice")
    def test_r03_share_returns_the_sorted_list(self): self.assertEqual(self.s.share(1, "carol", "write", "alice"), [{"user": "carol", "mode": "write"}]); self.assertEqual(self.s.share(1, "bob", "read", "alice"), [{"user": "bob", "mode": "read"}, {"user": "carol", "mode": "write"}]); self.assertEqual(self.s.shares(1, "alice"), self.s.shares(1))
    def test_r03_a_second_share_changes_the_mode(self): self.s.share(1, "bob", "read", "alice"); self.assertEqual(self.s.share(1, "bob", "write", "alice"), [{"user": "bob", "mode": "write"}])
    def test_r03_unshare(self):
        self.s.share(1, "bob", "read", "alice"); self.assertEqual(self.s.unshare(1, "bob", "alice"), [])
        with self.assertRaises(NotFound): self.s.unshare(1, "bob", "alice")
        with self.assertRaises(NotFound): self.s.get(1, "bob")
    def test_r03_invalid_shares(self):
        with self.assertRaises(ValidationError): self.s.share(1, "alice", "read", "alice")
        with self.assertRaises(ValidationError): self.s.share(1, "bob", "admin", "alice")
        with self.assertRaises(NotFound): self.s.share(1, "ghost", "read", "alice")
        with self.assertRaises(NotFound): self.s.share(9, "bob", "read", "alice")
    def test_r03_only_the_owner_manages_shares(self):
        self.s.share(1, "bob", "write", "alice")
        for call in (lambda: self.s.share(1, "carol", "read", "bob"), lambda: self.s.unshare(1, "bob", "bob"), lambda: self.s.shares(1, "bob")):
            with self.assertRaises(PermissionDenied): call()
        with self.assertRaises(NotFound): self.s.share(1, "alice", "read", "carol")
    def test_r03_shares_disappear_with_the_note(self): self.s.share(1, "bob", "read", "alice"); self.s.delete(1, "alice"); self.s.add("New", actor="alice"); self.assertEqual(self.s.shares(2, "alice"), [])
    def test_r03_shares_persist(self): self.s.share(1, "bob", "read", "alice"); self.assertEqual(NoteStore(self.path).shares(1), [{"user": "bob", "mode": "read"}])
    def test_r04_a_read_share_allows_reading_only(self):
        self.s.share(1, "bob", "read", "alice"); self.assertEqual(self.s.get(1, "bob")["body"], "secret"); self.assertEqual([n["id"] for n in self.s.list("bob")], [1]); self.assertEqual([n["id"] for n in self.s.search("secret", actor="bob")], [1]); self.assertEqual(self.s.tags("bob"), [("t", 1)])
        for call in (lambda: self.s.update(1, title="x", actor="bob"), lambda: self.s.delete(1, "bob")):
            with self.assertRaises(PermissionDenied) as cm: call()
            self.assertIn("bob", str(cm.exception)); self.assertIn("1", str(cm.exception))
        self.assertEqual(self.s.get(1)["title"], "Mine")
    def test_r04_a_write_share_allows_updates_but_not_delete(self):
        self.s.share(1, "bob", "write", "alice"); self.assertEqual(self.s.update(1, title="Edited", tags=["z"], actor="bob")["title"], "Edited"); self.assertEqual(self.s.get(1, "alice")["tags"], ["z"]); self.assertEqual(self.s.get(1)["owner"], "alice")
        with self.assertRaises(PermissionDenied): self.s.delete(1, "bob")
    def test_r04_shares_are_per_user(self): self.s.share(1, "bob", "read", "alice"); with_ = self.s.list("carol"); self.assertEqual(with_, []); self.assertEqual(self.s.list("alice")[0]["id"], 1)
    def test_r04_user_names_in_shares_match_ignoring_case(self): self.s.share(1, "BOB", "read", "alice"); self.assertEqual(self.s.get(1, "bob")["id"], 1); self.assertEqual(self.s.shares(1, "alice")[0]["user"], "bob")
    def test_r04_refused_operations_change_nothing(self):
        self.s.share(1, "bob", "read", "alice"); before = self.s.get(1)
        with self.assertRaises(PermissionDenied): self.s.update(1, title="x", body="y", tags=["q"], actor="bob")
        self.assertEqual(self.s.get(1), before)
    def test_r04_an_owner_may_always_update_and_delete(self): self.s.update(1, title="x", actor="alice"); self.s.delete(1, "alice"); self.assertEqual(self.s.list(), [])

class Api(Base):
    def setUp(self): super().setUp(); self.srv = make_server(self.s, auth=True).start(); self.addCleanup(self.srv.stop); self.tok = {n: self.call("POST", "/login", {"name": n, "password": "password1"}, None)[1]["token"] for n in ("alice", "bob", "carol")}
    def call(self, method, path, body=None, who="alice", raw=None, port=None, headers=None):
        h = dict(headers or {})
        if who: h["Authorization"] = "Bearer " + self.tok.get(who, who)
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode()); c = http.client.HTTPConnection("127.0.0.1", port or self.srv.port, timeout=60)
        try: c.request(method, path, body=data, headers=h); r = c.getresponse(); t = r.read().decode(); hd = {k.lower(): v for k, v in r.getheaders()}
        finally: c.close()
        return r.status, (json.loads(t) if t else None), hd

class TestAuthApi(Api):
    def test_r05_register_and_login(self):
        s, b, h = self.call("POST", "/register", {"name": "dave", "password": "password1"}, None); self.assertEqual((s, b), (201, {"name": "dave"})); self.assertEqual(self.call("POST", "/register", {"name": "dave", "password": "password1"}, None)[0], 422); self.assertEqual(self.call("POST", "/register", {"name": "x", "password": "password1"}, None)[0], 422)
        self.assertEqual(self.call("POST", "/login", {"name": "dave", "password": "password1"}, None)[0], 200); self.assertEqual(self.call("POST", "/login", {"name": "dave", "password": "bad"}, None)[:2], (401, {"error": "invalid credentials"}))
    def test_r05_authentication_is_required(self):
        for m, p in (("GET", "/notes"), ("POST", "/notes"), ("GET", "/notes/1"), ("GET", "/search"), ("POST", "/logout"), ("GET", "/notes/1/shares")):
            s, b, h = self.call(m, p, who=None); self.assertEqual((s, b, h["www-authenticate"]), (401, {"error": "unauthorized"}, "Bearer"), p)
    def test_r05_health_stays_open(self): self.assertEqual(self.call("GET", "/health", who=None)[:2], (200, {"status": "ok"}))
    def test_r05_bad_tokens(self):
        self.assertEqual(self.call("GET", "/notes", who="nope")[0], 401)
        for hdr in ("Basic x", "Bearer", self.tok["alice"]): self.assertEqual(self.call("GET", "/notes", who=None, headers={"Authorization": hdr})[0], 401)
    def test_r05_notes_belong_to_the_token_user(self):
        s, b, h = self.call("POST", "/notes", {"title": "mine"}, "alice"); self.assertEqual((s, b["owner"]), (201, "alice")); self.assertEqual(self.call("GET", "/notes", who="bob")[1]["total"], 0); self.assertEqual(self.call("GET", "/notes", who="alice")[1]["total"], 1); self.assertEqual(self.call("GET", "/search?q=mine", who="bob")[1]["total"], 0)
    def test_r05_invisible_notes_are_404_and_forbidden_ones_403(self):
        self.call("POST", "/notes", {"title": "mine"}, "alice"); self.assertEqual(self.call("GET", "/notes/1", who="bob")[:2], (404, {"error": "no note with id 1"})); self.assertEqual(self.call("DELETE", "/notes/1", who="bob")[0], 404)
        self.call("POST", "/notes/1/shares", {"user": "bob", "mode": "read"}, "alice"); self.assertEqual(self.call("GET", "/notes/1", who="bob")[0], 200); s, b, h = self.call("PUT", "/notes/1", {"title": "x"}, "bob"); self.assertEqual(s, 403); self.assertIn("bob", b["error"]); self.assertEqual(self.call("DELETE", "/notes/1", who="bob")[0], 403)
    def test_r05_share_routes(self):
        self.call("POST", "/notes", {"title": "mine"}, "alice"); s, b, h = self.call("POST", "/notes/1/shares", {"user": "bob", "mode": "write"}, "alice"); self.assertEqual((s, b), (200, {"items": [{"user": "bob", "mode": "write"}]})); self.assertEqual(self.call("GET", "/notes/1/shares", who="alice")[1]["items"][0]["user"], "bob")
        self.assertEqual(self.call("PUT", "/notes/1", {"title": "by bob"}, "bob")[0], 200); self.assertEqual(self.call("GET", "/notes/1/shares", who="bob")[0], 403); self.assertEqual(self.call("DELETE", "/notes/1/shares/bob", who="alice")[:2], (204, None)); self.assertEqual(self.call("GET", "/notes/1", who="bob")[0], 404); self.assertEqual(self.call("DELETE", "/notes/1/shares/bob", who="alice")[0], 404)
        self.assertEqual(self.call("POST", "/notes/1/shares", {"user": "bob", "mode": "admin"}, "alice")[0], 422); self.assertEqual(self.call("POST", "/notes/1/shares", {"user": "ghost", "mode": "read"}, "alice")[0], 404)
    def test_r05_logout(self): self.assertEqual(self.call("POST", "/logout", who="bob")[:2], (204, None)); self.assertEqual(self.call("GET", "/notes", who="bob")[0], 401)
    def test_r05_check_order(self): self.assertEqual(self.call("DELETE", "/notes", who=None)[0], 405); self.assertEqual(self.call("POST", "/notes", raw=b"{bad", who=None)[0], 401); self.assertEqual(self.call("POST", "/notes", raw=b"{bad")[0], 400); self.assertEqual(self.call("GET", "/nothing", who=None)[0], 404)
    def test_r05_stage_4_behaviour_through_a_token(self):
        for i in range(5): self.call("POST", "/notes", {"title": f"n{i}", "tags": ["x"]})
        s, b, h = self.call("GET", "/notes?limit=2&offset=1&tag=x"); self.assertEqual((s, b["total"], [n["title"] for n in b["items"]]), (200, 5, ["n1", "n2"])); self.assertEqual(self.call("GET", "/notes?limit=0")[0], 400); self.assertEqual(self.call("POST", "/notes", {"title": ""})[0], 422)
    def test_r05_without_auth_the_old_server_has_no_new_routes(self):
        with make_server(self.s) as plain:
            self.assertEqual(self.call("POST", "/login", {"name": "alice", "password": "password1"}, None, port=plain.port)[0], 404); self.assertEqual(self.call("POST", "/register", {}, None, port=plain.port)[0], 404); self.assertEqual(self.call("GET", "/notes", who=None, port=plain.port)[0], 200); self.assertEqual(self.call("POST", "/notes", {"title": "open"}, None, port=plain.port)[1]["owner"], None)

class TestUpgradeAndCli(unittest.TestCase):
    def setUp(self): self.dir = tempfile.mkdtemp(prefix="g5u-"); self.path = os.path.join(self.dir, "n.json")
    def tearDown(self): shutil.rmtree(self.dir, ignore_errors=True)
    def cli(self, *argv):
        env = dict(os.environ, NOTES_FILE=self.path, NOTES_NOW="2024-03-01T09:00:00", PYTHONPATH=WS); p = subprocess.run([sys.executable, "-m", "notes", *argv], capture_output=True, text=True, env=env, cwd=self.dir, timeout=60); return p.returncode, p.stdout, p.stderr
    def test_r06_a_stage_4_database_is_upgraded_on_opening(self):
        shutil.copyfile(os.path.join(HERE, "stage4.db"), self.path); s = NoteStore(self.path, clock=lambda: "2024-03-01T09:00:00")
        self.assertEqual([(n["id"], n["title"], n["body"], n["tags"], n["owner"], n["created"]) for n in s.list()], [(1, "Old one", "body one", ["x", "y"], None, "2024-02-01T10:00:00"), (3, "Old three", "", ["z"], None, "2024-02-01T10:00:00")]); self.assertEqual(s.schema_version(), 1)
        self.assertEqual(s.add("new", actor=None)["id"], 4); s.create_user("alice", "password1"); self.assertEqual([n["id"] for n in s.list("alice")], [1, 3, 4]); s.update(1, title="edited by alice", actor="alice"); self.assertEqual(s.get(1)["owner"], None); self.assertEqual(NoteStore(self.path).get(1)["title"], "edited by alice")
    def test_r06_opening_twice_is_harmless(self): shutil.copyfile(os.path.join(HERE, "stage4.db"), self.path); NoteStore(self.path).close(); NoteStore(self.path).close(); self.assertEqual(len(NoteStore(self.path).list()), 2)
    def test_r06_export_json_has_no_owner_key(self):
        s = NoteStore(self.path); s.create_user("alice", "password1"); s.add("A", actor="alice"); out = os.path.join(self.dir, "e.json"); s.export_json(out); n = json.load(open(out))["notes"][0]; self.assertNotIn("owner", n); self.assertEqual(n["title"], "A")
    def test_r06_adduser_and_share_commands(self):
        self.assertEqual(self.cli("adduser", "alice", "password1")[1], "Added user alice\n"); self.cli("adduser", "bob", "password1"); self.cli("add", "N"); self.assertEqual(self.cli("share", "1", "bob", "read")[1], "Shared note 1 with bob (read)\n")
    def test_r06_command_line_errors(self):
        rc, out, err = self.cli("adduser", "x", "password1"); self.assertEqual((rc, err), (2, "Error: a user name has 2-30 characters: letters, digits, '.', '_' and '-', starting with a letter\n")); self.cli("add", "N"); rc, out, err = self.cli("share", "1", "ghost", "read"); self.assertEqual(rc, 1); self.assertEqual(self.cli("share", "9", "bob", "read")[0], 1)
    def test_r06_stages_1_to_4_commands_still_work(self):
        self.assertEqual(self.cli("add", "Hello", "--tag", "t")[1], "Added note 1\n"); self.assertEqual(self.cli("list")[1], "  1  Hello  #t\n"); self.assertEqual(self.cli("stats")[1], "Notes: 1\nTags: 1\n"); self.assertEqual(self.cli("show", "9")[0], 1)
