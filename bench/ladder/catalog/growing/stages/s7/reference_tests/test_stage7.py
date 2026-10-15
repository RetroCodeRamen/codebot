import http.client, json, os, shutil, subprocess, sys, tempfile, unittest
from notes import NoteStore, NotFound, StoreError, ValidationError, make_server, sync_remote

WS = os.getcwd()
HERE = os.path.dirname(os.path.abspath(__file__))

class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="g7-"); self.t = {}
        self.a, self.b = self.dev("A", "2024-03-01T09:00:00"), self.dev("B", "2024-03-01T09:00:00")
    def tearDown(self): shutil.rmtree(self.dir, ignore_errors=True)
    def dev(self, name, now):
        clock = {"now": now}; s = NoteStore(os.path.join(self.dir, name + ".json"), clock=lambda c=clock: c["now"], device_id=name); s.clk = clock; return s
    def at(self, s, now): s.clk["now"] = now; return s
    def view(self, s):
        snap = s.snapshot(); return ([{k: v for k, v in n.items()} for n in snap["notes"]], snap["tombstones"])
    def same(self, x, y): self.assertEqual(self.view(x), self.view(y))
    def titles(self, s): return sorted(n["title"] for n in s.list())

class TestIdentity(Base):
    def test_r01_device_id(self): self.assertEqual(self.a.device_id, "A"); self.assertNotEqual(NoteStore(os.path.join(self.dir, "x.json")).device_id, "")
    def test_r01_the_device_id_is_kept(self): p = os.path.join(self.dir, "A.json"); self.a.close(); self.assertEqual(NoteStore(p).device_id, "A"); self.assertEqual(NoteStore(p, device_id="other").device_id, "A")
    def test_r01_random_ids_differ(self): self.assertNotEqual(NoteStore(os.path.join(self.dir, "p.json")).device_id, NoteStore(os.path.join(self.dir, "q.json")).device_id)
    def test_r01_uid_and_device_on_notes(self):
        n = self.a.add("One"); self.assertEqual(n["device"], "A"); self.assertTrue(isinstance(n["uid"], str) and len(n["uid"]) >= 8); self.assertNotEqual(self.a.add("Two")["uid"], n["uid"]); self.assertNotEqual(self.b.add("One")["uid"], n["uid"])
    def test_r01_update_sets_the_device_and_keeps_the_uid(self):
        n = self.a.add("One"); self.a.update(1, title="Uno"); self.assertEqual(self.a.get(1)["uid"], n["uid"])
    def test_r01_old_databases_get_uids(self):
        p = os.path.join(self.dir, "old.json"); shutil.copyfile(os.path.join(HERE, "stage6.db"), p); s = NoteStore(p, device_id="OLD"); notes = s.list(); self.assertEqual([(n["title"], n["owner"], n["device"]) for n in notes], [("Alice note", "alice", "OLD"), ("Plain", None, "OLD")]); self.assertEqual(len({n["uid"] for n in notes}), 2); self.assertEqual(s.schema_version(), 1)
        self.assertEqual(NoteStore(p).get(1)["uid"], notes[0]["uid"]); self.assertEqual(s.get(2)["reminder"], "2030-01-01T00:00:00"); self.assertEqual(s.tags(), [("a", 1), ("p", 1), ("q", 1)])
    def test_r01_export_json_format(self):
        self.a.add("One", actor=None); out = os.path.join(self.dir, "e.json"); self.a.export_json(out); self.assertEqual(sorted(json.load(open(out))["notes"][0]), ["body", "created", "id", "tags", "title", "updated"])

class TestSnapshots(Base):
    def test_r02_delete_leaves_a_tombstone(self):
        n = self.a.add("One"); self.at(self.a, "2024-03-02T10:00:00"); self.a.delete(1); self.assertEqual(self.a.snapshot()["tombstones"], [{"uid": n["uid"], "deleted": "2024-03-02T10:00:00", "device": "A"}]); self.assertEqual(self.a.snapshot()["notes"], [])
    def test_r03_snapshot_shape(self):
        self.a.create_user("alice", "password1"); self.a.add("One", "body", ["t"], actor="alice"); self.a.set_reminder(1, "2030-01-01T00:00:00"); sn = self.a.snapshot(); self.assertEqual(sn["device"], "A"); self.assertEqual(sorted(sn["notes"][0]), ["body", "created", "device", "tags", "title", "uid", "updated"]); json.dumps(sn)
    def test_r03_snapshots_are_sorted_by_uid(self):
        for i in range(8): self.a.add(f"n{i}")
        uids = [n["uid"] for n in self.a.snapshot()["notes"]]; self.assertEqual(uids, sorted(uids))
    def test_r03_apply_creates_notes_without_owner(self):
        self.a.create_user("alice", "password1"); self.a.add("One", "body", ["t", "u"], actor="alice"); r = self.b.apply_snapshot(self.a.snapshot()); self.assertEqual(r, {"applied": 1, "conflicts": 0}); n = self.b.get(1); self.assertEqual((n["title"], n["body"], n["tags"], n["owner"], n["device"]), ("One", "body", ["t", "u"], None, "A")); self.assertEqual(n["uid"], self.a.get(1)["uid"])
    def test_r03_apply_is_idempotent(self): self.a.add("One"); sn = self.a.snapshot(); self.b.apply_snapshot(sn); self.assertEqual(self.b.apply_snapshot(sn), {"applied": 0, "conflicts": 0}); self.assertEqual(len(self.b.list()), 1)
    def test_r03_a_newer_edit_wins_and_local_data_stays(self):
        self.b.create_user("bob", "password1"); self.a.add("One"); self.b.apply_snapshot(self.a.snapshot()); self.at(self.b, "2024-03-01T09:00:00")
        self.at(self.a, "2024-03-02T09:00:00"); self.a.update(1, title="Edited", body="new", tags=["z"]); r = self.b.apply_snapshot(self.a.snapshot()); self.assertEqual(r, {"applied": 1, "conflicts": 0}); n = self.b.get(1); self.assertEqual((n["title"], n["body"], n["tags"], n["updated"], n["device"], n["id"]), ("Edited", "new", ["z"], "2024-03-02T09:00:00", "A", 1))
    def test_r03_an_older_edit_loses(self):
        self.a.add("One"); self.b.apply_snapshot(self.a.snapshot()); self.at(self.b, "2024-03-03T09:00:00"); self.b.update(1, title="Mine"); self.at(self.a, "2024-03-02T09:00:00"); self.a.update(1, title="Theirs"); r = self.b.apply_snapshot(self.a.snapshot()); self.assertEqual(r, {"applied": 0, "conflicts": 0}); self.assertEqual(self.b.get(1)["title"], "Mine")
    def test_r03_a_newer_edit_over_an_unexchanged_local_edit_is_a_conflict(self):
        self.a.add("One"); self.b.apply_snapshot(self.a.snapshot()); self.at(self.b, "2024-03-02T09:00:00"); self.b.update(1, title="Mine"); self.at(self.a, "2024-03-03T09:00:00"); self.a.update(1, title="Theirs"); self.assertEqual(self.b.apply_snapshot(self.a.snapshot()), {"applied": 1, "conflicts": 1}); self.assertEqual(self.b.get(1)["title"], "Theirs")
    def test_r03_identical_concurrent_edits_lose_nothing(self):
        self.a.add("One"); self.b.apply_snapshot(self.a.snapshot()); self.at(self.b, "2024-03-02T09:00:00"); self.b.update(1, title="Same"); self.at(self.a, "2024-03-03T09:00:00"); self.a.update(1, title="Same"); self.assertEqual(self.b.apply_snapshot(self.a.snapshot()), {"applied": 1, "conflicts": 0})
    def test_r03_equal_times_are_decided_by_the_device(self):
        self.a.add("One"); self.b.apply_snapshot(self.a.snapshot()); self.at(self.a, "2024-03-02T09:00:00"); self.at(self.b, "2024-03-02T09:00:00"); self.a.update(1, title="from A"); self.b.update(1, title="from B"); self.a.sync_with(self.b); self.assertEqual(self.a.get(1)["title"], "from B"); self.same(self.a, self.b)
    def test_r03_a_newer_tombstone_deletes(self):
        self.a.add("One"); self.b.apply_snapshot(self.a.snapshot()); self.at(self.a, "2024-03-02T09:00:00"); self.a.delete(1); r = self.b.apply_snapshot(self.a.snapshot()); self.assertEqual(r["applied"], 1); self.assertEqual(self.b.list(), []); self.assertEqual(len(self.b.snapshot()["tombstones"]), 1)
        with self.assertRaises(NotFound): self.b.get(1)
    def test_r03_an_edit_after_the_delete_brings_the_note_back(self):
        self.a.add("One"); self.b.apply_snapshot(self.a.snapshot()); self.at(self.a, "2024-03-02T09:00:00"); self.a.delete(1); self.at(self.b, "2024-03-03T09:00:00"); self.b.update(1, title="Alive"); self.a.apply_snapshot(self.b.snapshot()); self.assertEqual(self.titles(self.a), ["Alive"]); self.assertEqual(self.a.snapshot()["tombstones"], []); self.assertEqual(self.b.apply_snapshot(self.a.snapshot())["applied"], 0)
    def test_r03_a_delete_after_the_edit_wins(self):
        self.a.add("One"); self.b.apply_snapshot(self.a.snapshot()); self.at(self.b, "2024-03-02T09:00:00"); self.b.update(1, title="Edited"); self.at(self.a, "2024-03-03T09:00:00"); self.a.delete(1); self.b.apply_snapshot(self.a.snapshot()); self.assertEqual(self.b.list(), [])
    def test_r03_tombstones_for_unknown_notes_are_stored(self):
        self.a.add("One"); self.at(self.a, "2024-03-02T09:00:00"); self.a.delete(1); r = self.b.apply_snapshot(self.a.snapshot()); self.assertEqual(r["applied"], 0); self.assertEqual(len(self.b.snapshot()["tombstones"]), 1); self.at(self.a, "2024-03-01T10:00:00")
        old = self.dev("C", "2024-03-01T09:30:00"); old.add("zombie"); sn = old.snapshot(); sn["notes"][0]["uid"] = self.a.snapshot()["tombstones"][0]["uid"]; self.assertEqual(self.b.apply_snapshot(sn)["applied"], 0); self.assertEqual(self.b.list(), [])
    def test_r03_invalid_snapshots_change_nothing(self):
        self.a.add("One"); good = self.a.snapshot(); bad = json.loads(json.dumps(good)); bad["notes"].append({"uid": "x", "title": "", "body": "", "tags": [], "created": "2024-03-01T09:00:00", "updated": "2024-03-01T09:00:00", "device": "A"})
        for sn in (bad, None, [], {"device": "A"}, {"device": "A", "notes": {}, "tombstones": []}, {"device": "A", "notes": [{"uid": "x"}], "tombstones": []}, {"device": "A", "notes": [], "tombstones": [{"uid": "x", "deleted": "soon", "device": "A"}]}):
            with self.assertRaises(ValidationError): self.b.apply_snapshot(sn)
        self.assertEqual(self.b.list(), []); self.assertEqual(self.b.snapshot()["tombstones"], [])
    def test_r03_a_device_cannot_apply_its_own_snapshot(self):
        with self.assertRaises(ValidationError): self.a.apply_snapshot(self.a.snapshot())
    def test_r03_normal_operations_keep_working_on_synced_notes(self): self.a.add("One", tags=["x"]); self.b.apply_snapshot(self.a.snapshot()); self.assertEqual([n["id"] for n in self.b.search("one", ["x"])], [1]); self.b.update(1, title="Two"); self.assertEqual(self.b.add("Three")["id"], 2); self.b.delete(1)

class TestSync(Base):
    def test_r04_basic_two_way_merge(self):
        self.a.add("only A"); self.b.add("only B"); r = self.a.sync_with(self.b); self.assertEqual(r, {"pulled": 1, "pushed": 1, "conflicts": 0}); self.assertEqual(self.titles(self.a), ["only A", "only B"]); self.same(self.a, self.b)
    def test_r04_second_sync_does_nothing(self):
        self.a.add("x"); self.b.add("y"); self.a.sync_with(self.b); before = self.view(self.a); self.assertEqual(self.a.sync_with(self.b), {"pulled": 0, "pushed": 0, "conflicts": 0}); self.assertEqual(self.b.sync_with(self.a), {"pulled": 0, "pushed": 0, "conflicts": 0}); self.assertEqual(self.view(self.a), before)
    def test_r04_direction_does_not_matter(self):
        for s, w in ((self.a, "A"), (self.b, "B")): s.add("shared base" if w == "A" else "b1")
        c, d = self.dev("C", "2024-03-01T09:00:00"), self.dev("D", "2024-03-01T09:00:00"); c.apply_snapshot(self.a.snapshot()); d.apply_snapshot(self.a.snapshot()); self.at(c, "2024-03-02T09:00:00"); self.at(d, "2024-03-02T09:30:00"); c.update(1, title="from C"); d.update(1, title="from D"); c.sync_with(d); self.assertEqual(c.get(1)["title"], "from D")
        x, y = self.dev("X", "2024-03-01T09:00:00"), self.dev("Y", "2024-03-01T09:00:00"); x.apply_snapshot(self.a.snapshot()); y.apply_snapshot(self.a.snapshot()); self.at(x, "2024-03-02T09:00:00"); self.at(y, "2024-03-02T09:30:00"); x.update(1, title="from X"); y.update(1, title="from Y"); y.sync_with(x); self.assertEqual(x.get(1)["title"], "from Y")
    def test_r04_conflicts_are_reported(self):
        self.a.add("One"); self.a.sync_with(self.b); self.at(self.a, "2024-03-02T09:00:00"); self.at(self.b, "2024-03-02T10:00:00"); self.a.update(1, title="A edit"); self.b.update(1, title="B edit"); r = self.a.sync_with(self.b); self.assertEqual(r, {"pulled": 1, "pushed": 0, "conflicts": 1}); self.assertEqual(self.a.get(1)["title"], "B edit"); self.at(self.b, "2024-03-04T09:00:00"); self.b.update(1, title="B again"); self.assertEqual(self.a.sync_with(self.b), {"pulled": 1, "pushed": 0, "conflicts": 0}); self.same(self.a, self.b)
    def test_r04_deletes_travel(self):
        self.a.add("One"); self.a.add("Two"); self.a.sync_with(self.b); self.at(self.b, "2024-03-02T09:00:00"); self.b.delete([n["id"] for n in self.b.list() if n["title"] == "One"][0]); r = self.a.sync_with(self.b); self.assertEqual((r["pulled"], r["pushed"]), (1, 0)); self.assertEqual(self.titles(self.a), ["Two"]); self.same(self.a, self.b)
    def test_r04_a_deleted_note_is_not_resurrected_by_a_stale_device(self):
        self.a.add("One"); self.a.sync_with(self.b); c = self.dev("C", "2024-03-01T09:00:00"); c.sync_with(self.a); self.at(self.b, "2024-03-02T09:00:00"); self.b.delete(1); self.b.sync_with(self.a); c.sync_with(self.a); self.assertEqual(c.list(), []); self.assertEqual(self.a.list(), [])
    def test_r04_three_devices_converge(self):
        c = self.dev("C", "2024-03-01T09:00:00"); self.a.add("a1"); self.b.add("b1"); c.add("c1"); self.a.sync_with(self.b); self.b.sync_with(c); c.sync_with(self.a); self.a.sync_with(self.b)
        self.assertEqual(self.titles(self.a), ["a1", "b1", "c1"]); self.same(self.a, self.b); self.same(self.b, c)
    def test_r04_ids_are_local_and_survive(self):
        self.a.add("a1"); self.b.add("b1"); self.b.add("b2"); self.a.sync_with(self.b); self.assertEqual(sorted(n["id"] for n in self.a.list()), [1, 2, 3]); self.assertEqual(self.a.add("a2")["id"], 4)
    def test_r04_tags_and_order_travel(self): self.a.add("t", tags=["z", "a", "m"]); self.a.sync_with(self.b); self.assertEqual(self.b.get(1)["tags"], ["z", "a", "m"])
    def test_r04_local_data_is_not_synced(self):
        self.a.create_user("alice", "password1"); self.a.add("secret", actor="alice"); self.a.set_reminder(1, "2030-01-01T00:00:00"); self.a.sync_with(self.b); n = self.b.get(1); self.assertEqual((n["owner"], n["reminder"]), (None, None)); self.assertEqual(self.a.get(1)["owner"], "alice")
    def test_r04_syncing_with_yourself(self):
        with self.assertRaises(ValidationError): self.a.sync_with(self.a)
        twin = NoteStore(os.path.join(self.dir, "twin.json"), device_id="A")
        with self.assertRaises(ValidationError): self.a.sync_with(twin)
        with self.assertRaises(ValidationError): self.a.sync_with("B")
    def test_r04_sync_is_atomic_for_a_bad_peer(self):
        self.a.add("x"); self.b.add("y"); n0 = len(self.a.list())
        class Bad(NoteStore):
            def snapshot(self): sn = super().snapshot(); sn["notes"].append({"uid": "bad"}); return sn
        bad = Bad(os.path.join(self.dir, "bad.json"), device_id="Z")
        with self.assertRaises(ValidationError): self.a.sync_with(bad)
        self.assertEqual(len(self.a.list()), n0)
    def test_r04_many_notes(self):
        for i in range(60): self.a.add(f"a{i}", tags=[f"t{i % 5}"]); self.b.add(f"b{i}")
        r = self.a.sync_with(self.b); self.assertEqual((r["pulled"], r["pushed"]), (60, 60)); self.assertEqual(len(self.b.list()), 120); self.same(self.a, self.b)

class TestHttp(Base):
    def call(self, method, path, body=None, raw=None, srv=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode()); c = http.client.HTTPConnection("127.0.0.1", (srv or self.srv).port, timeout=60)
        try: c.request(method, path, body=data, headers={"Content-Type": "application/json"}); r = c.getresponse(); t = r.read().decode()
        finally: c.close()
        return r.status, (json.loads(t) if t else None)
    def setUp(self): super().setUp(); self.srv = make_server(self.b).start(); self.addCleanup(self.srv.stop)
    def test_r05_routes(self):
        self.b.add("on B"); s, sn = self.call("GET", "/sync/snapshot"); self.assertEqual((s, sn["device"], len(sn["notes"])), (200, "B", 1)); self.a.add("on A"); self.assertEqual(self.call("POST", "/sync/apply", self.a.snapshot()), (200, {"applied": 1, "conflicts": 0})); self.assertEqual(self.titles(self.b), ["on A", "on B"])
        self.assertEqual(self.call("POST", "/sync/apply", {"device": "A", "notes": [{}], "tombstones": []})[0], 422); self.assertEqual(self.call("POST", "/sync/apply", raw=b"[1]")[0], 400); self.assertEqual(self.call("POST", "/sync/snapshot", {})[0], 405); self.assertEqual(self.call("GET", "/sync/apply")[0], 405)
    def test_r05_no_sync_routes_with_auth(self):
        with make_server(self.b, auth=True) as srv: self.assertEqual(self.call("GET", "/sync/snapshot", srv=srv)[0], 404); self.assertEqual(self.call("POST", "/sync/apply", {}, srv=srv)[0], 404)
    def test_r05_sync_remote(self):
        self.a.add("on A"); self.b.add("on B"); r = sync_remote(self.a, f"http://127.0.0.1:{self.srv.port}"); self.assertEqual(r, {"pulled": 1, "pushed": 1, "conflicts": 0}); self.same(self.a, self.b); self.assertEqual(sync_remote(self.a, f"http://127.0.0.1:{self.srv.port}/"), {"pulled": 0, "pushed": 0, "conflicts": 0})
    def test_r05_sync_remote_errors(self):
        for url in ("http://127.0.0.1:1", f"http://127.0.0.1:{self.srv.port}/nope"):
            with self.assertRaises(StoreError) as cm: sync_remote(self.a, url, timeout=5)
            self.assertTrue(str(cm.exception).startswith("sync failed"))

class TestCli(Base):
    def cli(self, path, *argv):
        env = dict(os.environ, NOTES_FILE=path, NOTES_NOW="2024-03-01T09:00:00", PYTHONPATH=WS); p = subprocess.run([sys.executable, "-m", "notes", *argv], capture_output=True, text=True, env=env, cwd=self.dir, timeout=120); return p.returncode, p.stdout, p.stderr
    def test_r06_device_and_file_sync(self):
        p1, p2 = os.path.join(self.dir, "one.db"), os.path.join(self.dir, "two.db"); d1, d2 = self.cli(p1, "device")[1].strip(), self.cli(p2, "device")[1].strip(); self.assertTrue(d1 and d2 and d1 != d2); self.assertEqual(self.cli(p1, "device")[1].strip(), d1)
        self.cli(p1, "add", "from one"); self.cli(p2, "add", "from two"); rc, out, err = self.cli(p1, "sync", p2); self.assertEqual((rc, out), (0, f"Synced with {p2}: pulled 1, pushed 1, conflicts 0\n")); self.assertEqual(self.cli(p2, "list")[1].count("\n"), 2); self.assertEqual(self.cli(p1, "sync", p2)[1], f"Synced with {p2}: pulled 0, pushed 0, conflicts 0\n")
    def test_r06_sync_with_a_server(self):
        self.b.add("served"); p = os.path.join(self.dir, "c.db")
        with make_server(self.b) as srv: url = f"http://127.0.0.1:{srv.port}"; rc, out, err = self.cli(p, "sync", url); self.assertEqual((rc, out), (0, f"Synced with {url}: pulled 1, pushed 0, conflicts 0\n"))
        self.assertEqual(self.cli(p, "list")[1], "  1  served\n")
    def test_r06_sync_with_a_dead_server(self): rc, out, err = self.cli(os.path.join(self.dir, "d.db"), "sync", "http://127.0.0.1:1"); self.assertEqual(rc, 2); self.assertTrue(err.startswith("Error: sync failed"))
    def test_r06_older_commands_still_work(self):
        p = os.path.join(self.dir, "e.db"); self.assertEqual(self.cli(p, "add", "Hello", "--tag", "t")[1], "Added note 1\n"); self.assertEqual(self.cli(p, "list")[1], "  1  Hello  #t\n"); self.assertEqual(self.cli(p, "adduser", "alice", "password1")[1], "Added user alice\n"); self.assertEqual(self.cli(p, "remind", "1", "2024-01-01T00:00:00")[0], 0); self.assertEqual(self.cli(p, "worker", "--once")[1], "Fired 1 reminder(s)\n"); self.assertEqual(self.cli(p, "delete", "1")[1], "Deleted note 1\n")
