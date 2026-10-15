import http.client, json, os, shutil, signal, subprocess, sys, tempfile, threading, time, unittest
from notes import NoteStore, NotFound, PermissionDenied, ValidationError, Worker, make_server

WS = os.getcwd()
HERE = os.path.dirname(os.path.abspath(__file__))

class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="g6-"); self.path = os.path.join(self.dir, "n.json"); self.now = "2024-03-01T09:00:00"; self.s = NoteStore(self.path, clock=lambda: self.now)
        for n in ("alice", "bob"): self.s.create_user(n, "password1")
    def tearDown(self): shutil.rmtree(self.dir, ignore_errors=True)
    def store(self): return NoteStore(self.path, clock=lambda: self.now)

class TestReminders(Base):
    def test_r01_default_is_none(self): self.assertIsNone(self.s.add("A")["reminder"])
    def test_r01_set_and_clear(self):
        self.s.add("A"); n = self.s.set_reminder(1, "2024-03-02T10:00:00"); self.assertEqual(n["reminder"], "2024-03-02T10:00:00"); self.assertEqual(self.s.get(1)["reminder"], "2024-03-02T10:00:00"); self.assertIsNone(self.s.clear_reminder(1)["reminder"])
    def test_r01_past_times_are_fine(self): self.s.add("A"); self.assertEqual(self.s.set_reminder(1, "2020-01-01T00:00:00")["reminder"], "2020-01-01T00:00:00")
    def test_r01_invalid_times(self):
        self.s.add("A")
        for bad in ("tomorrow", "2024-03-02", "2024-03-02 10:00:00", "2024-13-02T10:00:00", "2024-02-30T10:00:00", "2024-03-02T25:00:00", "", None, 5):
            with self.assertRaises(ValidationError, msg=repr(bad)): self.s.set_reminder(1, bad)
        self.assertIsNone(self.s.get(1)["reminder"])
    def test_r01_missing_note(self):
        with self.assertRaises(NotFound): self.s.set_reminder(9, "2024-03-02T10:00:00")
        with self.assertRaises(NotFound): self.s.clear_reminder(9)
    def test_r01_access_rules(self):
        self.s.add("A", actor="alice")
        with self.assertRaises(NotFound): self.s.set_reminder(1, "2024-03-02T10:00:00", "bob")
        self.s.share(1, "bob", "read", "alice")
        with self.assertRaises(PermissionDenied): self.s.set_reminder(1, "2024-03-02T10:00:00", "bob")
        self.s.share(1, "bob", "write", "alice"); self.assertEqual(self.s.set_reminder(1, "2024-03-02T10:00:00", "bob")["reminder"], "2024-03-02T10:00:00")
    def test_r01_reminders_persist(self): self.s.add("A"); self.s.set_reminder(1, "2024-03-02T10:00:00"); self.assertEqual(self.store().get(1)["reminder"], "2024-03-02T10:00:00")
    def test_r01_updating_other_fields_keeps_the_reminder(self): self.s.add("A"); self.s.set_reminder(1, "2024-03-02T10:00:00"); self.s.update(1, title="B"); self.assertEqual(self.s.get(1)["reminder"], "2024-03-02T10:00:00")

class TestFiring(Base):
    def test_r02_due_reminders_fire_once(self):
        self.s.add("Pay rent", actor="alice"); self.s.set_reminder(1, "2024-03-01T08:00:00"); self.assertEqual(self.s.fire_due(), [1]); self.assertEqual(self.s.fire_due(), [])
        n = self.s.notifications(); self.assertEqual([(x["note_id"], x["user"], x["message"], x["ts"], x["read"]) for x in n], [(1, "alice", "Reminder: Pay rent", "2024-03-01T09:00:00", False)])
    def test_r02_only_due_reminders_fire(self):
        for i, due in enumerate(("2024-03-01T09:00:00", "2024-03-01T09:00:01", "2024-02-01T00:00:00")): self.s.add(f"n{i}"); self.s.set_reminder(i + 1, due)
        self.assertEqual(self.s.fire_due(), [1, 3]); self.now = "2024-03-01T09:00:01"; self.assertEqual(self.s.fire_due(), [2])
    def test_r02_an_explicit_now(self): self.s.add("A"); self.s.set_reminder(1, "2025-01-01T00:00:00"); self.assertEqual(self.s.fire_due(), []); self.assertEqual(self.s.fire_due("2025-01-01T00:00:00"), [1]); self.assertEqual(self.s.notifications()[0]["ts"], "2025-01-01T00:00:00")
    def test_r02_notes_without_an_owner_notify_nobody_in_particular(self): self.s.add("Shared"); self.s.set_reminder(1, "2024-01-01T00:00:00"); self.s.fire_due(); self.assertIsNone(self.s.notifications()[0]["user"])
    def test_r02_ids_come_back_in_order(self):
        for i in (3, 1, 2): self.s.add(f"n{i}")
        for i in (3, 2, 1): self.s.set_reminder(i, "2024-01-01T00:00:00")
        self.assertEqual(self.s.fire_due(), [1, 2, 3])
    def test_r02_rearming_after_a_change(self): self.s.add("A"); self.s.set_reminder(1, "2024-01-01T00:00:00"); self.s.fire_due(); self.s.set_reminder(1, "2024-01-01T00:00:00"); self.assertEqual(self.s.fire_due(), [1]); self.assertEqual(len(self.s.notifications()), 2)
    def test_r02_clearing_stops_a_pending_reminder(self): self.s.add("A"); self.s.set_reminder(1, "2024-01-01T00:00:00"); self.s.clear_reminder(1); self.assertEqual(self.s.fire_due(), [])
    def test_r02_deleting_drops_the_pending_reminder_but_keeps_notifications(self):
        self.s.add("A"); self.s.add("B"); self.s.set_reminder(1, "2024-01-01T00:00:00"); self.s.set_reminder(2, "2024-01-01T00:00:00"); self.s.fire_due(); self.s.add("C"); self.s.set_reminder(3, "2024-01-01T00:00:00"); self.s.delete(3); self.assertEqual(self.s.fire_due(), []); self.s.delete(1); self.assertEqual(len(self.s.notifications()), 2)
    def test_r02_firing_ignores_the_actor_rules(self): self.s.add("A", actor="alice"); self.s.set_reminder(1, "2024-01-01T00:00:00"); self.assertEqual(self.s.fire_due(), [1])
    def test_r02_exactly_once_under_concurrency(self):
        for i in range(30): self.s.add(f"n{i}", actor="alice"); self.s.set_reminder(i + 1, "2024-01-01T00:00:00")
        results, lock = [], threading.Lock()
        def work():
            st = self.store()
            try:
                for _ in range(3):
                    r = st.fire_due()
                    with lock: results.extend(r)
            finally: st.close()
        ts = [threading.Thread(target=work) for _ in range(6)]; [t.start() for t in ts]; [t.join(120) for t in ts]; self.assertEqual(sorted(results), list(range(1, 31))); self.assertEqual(len(self.s.notifications()), 30)
    def test_r02_exactly_once_across_processes(self):
        for i in range(20): self.s.add(f"n{i}"); self.s.set_reminder(i + 1, "2024-01-01T00:00:00")
        code = "import sys, os, json; sys.path.insert(0, os.getcwd()); import notes\ns = notes.NoteStore(sys.argv[1], clock=lambda: '2024-03-01T09:00:00')\nprint(json.dumps(s.fire_due()))\n"
        ps = [subprocess.Popen([sys.executable, "-c", code, self.path], cwd=WS, stdout=subprocess.PIPE, text=True) for _ in range(4)]; fired = []
        for p in ps: out, _ = p.communicate(120); fired += json.loads(out)
        self.assertEqual(sorted(fired), list(range(1, 21))); self.assertEqual(len(self.s.notifications()), 20)

class TestNotifications(Base):
    def setUp(self):
        super().setUp(); self.s.add("A1", actor="alice"); self.s.add("B1", actor="bob"); self.s.add("A2", actor="alice")
        for i in (1, 2, 3): self.s.set_reminder(i, "2024-01-01T00:00:00")
        self.s.fire_due()
    def test_r03_all_and_per_user(self): self.assertEqual(len(self.s.notifications()), 3); self.assertEqual([n["note_id"] for n in self.s.notifications("alice")], [1, 3]); self.assertEqual([n["note_id"] for n in self.s.notifications("BOB")], [2]); self.assertEqual(self.s.notifications("carol"), [])
    def test_r03_mark_read(self):
        self.assertEqual(self.s.mark_read([1, 2]), 2); self.assertEqual(self.s.mark_read([1, 2]), 0); self.assertEqual([n["id"] for n in self.s.notifications(unread_only=True)], [3]); self.assertEqual([n["read"] for n in self.s.notifications()], [True, True, False])
    def test_r03_mark_read_for_a_user_touches_only_their_own(self): self.assertEqual(self.s.mark_read([1, 2, 3], "alice"), 2); self.assertEqual([n["id"] for n in self.s.notifications(unread_only=True)], [2])
    def test_r03_odd_ids_are_ignored(self): self.assertEqual(self.s.mark_read([99, "1", None, True, 1.5]), 0); self.assertEqual(len(self.s.notifications(unread_only=True)), 3)
    def test_r03_unread_for_a_user(self): self.s.mark_read([1]); self.assertEqual([n["id"] for n in self.s.notifications("alice", True)], [3])
    def test_r03_notifications_persist(self): self.s.mark_read([1]); self.assertEqual([n["read"] for n in self.store().notifications()], [True, False, False])

class TestWorker(Base):
    def test_r04_run_once_reports(self):
        self.s.add("A"); self.s.set_reminder(1, "2024-01-01T00:00:00"); self.s.login("alice", "password1"); self.now = "2024-03-05T09:00:00"; w = Worker(self.s); r = w.run_once(); self.assertEqual(r, {"fired": [1], "tokens_purged": 1}); self.assertEqual((w.runs, w.fired_total, w.last_report), (1, 1, r)); self.assertEqual(w.run_once(), {"fired": [], "tokens_purged": 0}); self.assertEqual((w.runs, w.fired_total), (2, 1))
    def test_r04_purge_tokens_boundary(self):
        tok = self.s.login("alice", "password1"); self.now = "2024-03-02T08:59:59"; self.assertEqual(self.s.purge_tokens(), 0); self.now = "2024-03-02T09:00:00"; self.assertEqual(self.s.purge_tokens(), 1); self.assertEqual(self.s.purge_tokens(), 0)
    def test_r04_a_worker_clock_overrides_the_store_clock(self): self.s.add("A"); self.s.set_reminder(1, "2030-01-01T00:00:00"); self.assertEqual(Worker(self.s, clock=lambda: "2031-01-01T00:00:00").run_once()["fired"], [1])
    def test_r04_the_thread_runs_immediately_and_repeatedly(self):
        self.s.add("A"); self.s.set_reminder(1, "2024-01-01T00:00:00"); w = Worker(self.s, interval=0.05); self.assertIs(w.start(), w); end = time.time() + 15
        while w.runs < 3 and time.time() < end: time.sleep(0.02)
        w.stop(); self.assertGreaterEqual(w.runs, 3); self.assertEqual(w.fired_total, 1); self.assertEqual(len(self.s.notifications()), 1)
    def test_r04_reminders_added_later_are_picked_up(self):
        w = Worker(self.s, interval=0.05).start()
        try:
            self.s.add("late"); self.s.set_reminder(1, "2024-01-01T00:00:00"); end = time.time() + 15
            while not self.s.notifications() and time.time() < end: time.sleep(0.02)
            self.assertEqual(len(self.s.notifications()), 1)
        finally: w.stop()
    def test_r04_start_twice_keeps_one_thread(self): w = Worker(self.s, interval=0.05); w.start(); th = w._thread; w.start(); self.assertIs(w._thread, th); w.stop()
    def test_r04_stop_without_start_and_restart(self): w = Worker(self.s, interval=0.05); w.stop(); w.start(); w.stop(); w.start(); w.stop()
    def test_r04_errors_in_a_run_do_not_stop_the_thread(self):
        w = Worker(self.s, interval=0.05); orig = w.run_once; n = {"c": 0}
        def flaky(now=None):
            n["c"] += 1
            if n["c"] == 1: raise RuntimeError("first run fails")
            return orig(now)
        w.run_once = flaky; w.start(); end = time.time() + 15
        while n["c"] < 3 and time.time() < end: time.sleep(0.02)
        w.stop(); self.assertGreaterEqual(n["c"], 3); self.assertEqual(w.last_error, "RuntimeError: first run fails")
    def test_r04_two_workers_fire_each_reminder_once(self):
        for i in range(10): self.s.add(f"n{i}"); self.s.set_reminder(i + 1, "2024-01-01T00:00:00")
        a, b = Worker(self.store(), interval=0.02).start(), Worker(self.store(), interval=0.02).start(); end = time.time() + 15
        while a.fired_total + b.fired_total < 10 and time.time() < end: time.sleep(0.02)
        time.sleep(0.2); a.stop(); b.stop(); self.assertEqual(a.fired_total + b.fired_total, 10); self.assertEqual(len(self.s.notifications()), 10)

class TestHttp(Base):
    def setUp(self): super().setUp(); self.srv = make_server(self.s).start(); self.addCleanup(self.srv.stop)
    def call(self, method, path, body=None, raw=None, port=None, tok=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode()); c = http.client.HTTPConnection("127.0.0.1", port or self.srv.port, timeout=60); h = {"Authorization": "Bearer " + tok} if tok else {}
        try: c.request(method, path, body=data, headers=h); r = c.getresponse(); t = r.read().decode()
        finally: c.close()
        return r.status, (json.loads(t) if t else None)
    def test_r05_reminder_routes(self):
        self.s.add("A"); s, b = self.call("PUT", "/notes/1/reminder", {"due": "2024-03-02T10:00:00"}); self.assertEqual((s, b["reminder"]), (200, "2024-03-02T10:00:00")); s, b = self.call("DELETE", "/notes/1/reminder"); self.assertEqual((s, b["reminder"]), (200, None))
        self.assertEqual(self.call("PUT", "/notes/1/reminder", {"due": "soon"})[0], 422); self.assertEqual(self.call("PUT", "/notes/9/reminder", {"due": "2024-03-02T10:00:00"})[0], 404); self.assertEqual(self.call("POST", "/notes/1/reminder", {})[0], 405); self.assertEqual(self.call("PUT", "/notes/1/reminder", raw=b"{bad")[0], 400)
    def test_r05_notifications_routes(self):
        for i in range(2): self.s.add(f"n{i}"); self.s.set_reminder(i + 1, "2024-01-01T00:00:00")
        self.s.fire_due(); s, b = self.call("GET", "/notifications"); self.assertEqual((s, b["total"], b["items"][0]["message"]), (200, 2, "Reminder: n0")); self.assertEqual(self.call("POST", "/notifications/read", {"ids": [1]}), (200, {"marked": 1})); self.assertEqual(self.call("GET", "/notifications?unread=1")[1]["total"], 1); self.assertEqual(self.call("POST", "/notifications/read", {"ids": "x"})[0], 422)
    def test_r05_authenticated_users_only_see_their_own(self):
        with make_server(self.s, auth=True) as srv:
            toks = {n: self.call("POST", "/login", {"name": n, "password": "password1"}, port=srv.port)[1]["token"] for n in ("alice", "bob")}
            for n in ("alice", "bob"): self.call("POST", "/notes", {"title": f"{n} note"}, port=srv.port, tok=toks[n])
            for i in (1, 2): self.s.set_reminder(i, "2024-01-01T00:00:00")
            self.s.fire_due(); self.assertEqual([x["note_id"] for x in self.call("GET", "/notifications", port=srv.port, tok=toks["alice"])[1]["items"]], [1]); self.assertEqual(self.call("POST", "/notifications/read", {"ids": [1, 2]}, port=srv.port, tok=toks["alice"])[1], {"marked": 1}); self.assertEqual(self.call("GET", "/notifications", port=srv.port)[0], 401)

class TestCli(Base):
    def cli(self, *argv, timeout=60):
        env = dict(os.environ, NOTES_FILE=self.path, NOTES_NOW=self.now, PYTHONPATH=WS); p = subprocess.run([sys.executable, "-m", "notes", *argv], capture_output=True, text=True, env=env, cwd=self.dir, timeout=timeout); return p.returncode, p.stdout, p.stderr
    def test_r06_remind_and_worker_once(self):
        self.cli("add", "Pay rent"); self.assertEqual(self.cli("remind", "1", "2024-03-01T08:00:00")[1], "Reminder set for note 1 at 2024-03-01T08:00:00\n"); self.assertEqual(self.cli("worker", "--once")[1], "Fired 1 reminder(s)\n"); self.assertEqual(self.cli("worker", "--once")[1], "Fired 0 reminder(s)\n")
        self.assertEqual(self.cli("notifications")[1], "[1] 2024-03-01T09:00:00 Reminder: Pay rent (new)\n"); self.assertEqual(self.cli("notifications", "--unread")[1].count("\n"), 1); self.assertEqual(self.cli("remind", "1", "--clear")[1], "Reminder cleared for note 1\n")
    def test_r06_command_errors(self):
        self.cli("add", "A"); self.assertEqual(self.cli("remind", "1")[:1], (2,)); self.assertEqual(self.cli("remind", "1", "soon")[2], "Error: a reminder time looks like 2024-03-01T09:00:00\n"); self.assertEqual(self.cli("remind", "9", "2024-03-01T09:00:00")[0], 1)
    def test_r06_no_notifications(self): self.assertEqual(self.cli("notifications")[1], "No notifications.\n"); self.assertEqual(self.cli("notifications", "--user", "alice")[1], "No notifications.\n")
    def test_r06_notifications_for_a_user(self): self.s.add("A", actor="alice"); self.s.set_reminder(1, "2024-01-01T00:00:00"); self.s.fire_due(); self.assertEqual(self.cli("notifications", "--user", "ALICE")[1].count("\n"), 1); self.assertEqual(self.cli("notifications", "--user", "bob")[1], "No notifications.\n")
    def test_r06_the_long_running_worker(self):
        self.s.add("A"); self.s.set_reminder(1, "2024-01-01T00:00:00"); env = dict(os.environ, NOTES_FILE=self.path, NOTES_NOW=self.now, PYTHONPATH=WS); p = subprocess.Popen([sys.executable, "-m", "notes", "worker", "--interval", "0.1"], env=env, cwd=self.dir, stdout=subprocess.PIPE, text=True)
        try:
            first = p.stdout.readline().strip(); second = p.stdout.readline().strip(); self.assertEqual((first, second), ("Worker running", "Fired reminder of note 1"))
        finally: p.send_signal(signal.SIGTERM); p.wait(30)
    def test_r06_stage_5_databases_open_unchanged(self):
        shutil.copyfile(os.path.join(HERE, "stage5.db"), self.path); s = self.store(); self.assertEqual([(n["id"], n["owner"], n["reminder"]) for n in s.list()], [(1, "alice", None), (2, "alice", None), (3, None, None)]); self.assertEqual(s.shares(2, "alice"), [{"user": "bob", "mode": "read"}]); self.assertEqual(s.schema_version(), 1)
        s.set_reminder(1, "2024-01-01T00:00:00"); self.assertEqual(s.fire_due(), [1]); self.assertEqual(s.notifications()[0]["user"], "alice"); self.assertTrue(s.login("bob", "password1"))
    def test_r06_export_json_stays_in_the_stage_2_format(self): self.s.add("A", actor="alice"); self.s.set_reminder(1, "2024-01-01T00:00:00"); out = os.path.join(self.dir, "e.json"); self.s.export_json(out); n = json.load(open(out))["notes"][0]; self.assertEqual(sorted(n), ["body", "created", "id", "tags", "title", "updated"])
    def test_r06_older_commands_still_work(self):
        self.assertEqual(self.cli("add", "Hello", "--tag", "t")[1], "Added note 1\n"); self.assertEqual(self.cli("list")[1], "  1  Hello  #t\n"); self.assertEqual(self.cli("adduser", "carol", "password1")[1], "Added user carol\n"); self.assertEqual(self.cli("stats")[1], "Notes: 1\nTags: 1\n")
