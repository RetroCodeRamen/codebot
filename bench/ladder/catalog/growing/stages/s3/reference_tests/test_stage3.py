import json, os, sqlite3, subprocess, sys, tempfile, threading, unittest
from notes import NoteStore, StoreError, NotFound, ValidationError

WS = os.getcwd()
LEGACY = {"next_id": 9, "notes": [{"id": 2, "title": "Old one", "body": "b1", "tags": ["x", "y"], "created": "2024-01-01T00:00:00", "updated": "2024-01-02T00:00:00"}, {"id": 5, "title": "Old two", "body": "", "created": "2024-01-03T00:00:00", "updated": "2024-01-03T00:00:00"}]}

class Base(unittest.TestCase):
    def setUp(self): self.dir = tempfile.mkdtemp(prefix="g3-"); self.path = os.path.join(self.dir, "n.json"); self.now = "2024-03-01T09:00:00"
    def tearDown(self):
        import shutil; shutil.rmtree(self.dir, ignore_errors=True)
    def store(self, path=None): return NoteStore(path or self.path, clock=lambda: self.now)
    def legacy(self, doc=None, path=None): json.dump(doc or LEGACY, open(path or self.path, "w"))

class TestSqlite(Base):
    def test_r01_the_file_is_an_sqlite_database(self): s = self.store(); s.add("A"); self.assertEqual(open(self.path, "rb").read(16)[:15], b"SQLite format 3")
    def test_r01_schema_version(self): self.assertEqual(self.store().schema_version(), 1)
    def test_r01_the_database_has_a_meta_table_with_the_version(self):
        self.store(); c = sqlite3.connect(self.path); self.addCleanup(c.close); names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}; self.assertIn("meta", names); self.assertIn("1", [str(r[1]) for r in c.execute("SELECT * FROM meta")])
    def test_r01_foreign_keys_and_busy_timeout(self): s = self.store(); self.assertEqual(s.db.execute("PRAGMA foreign_keys").fetchone()[0], 1); self.assertGreaterEqual(s.db.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
    def test_r01_the_public_interface_is_unchanged(self):
        s = self.store(); n = s.add("Hello", "World", ["a"]); self.assertEqual((n["id"], n["tags"], n["created"]), (1, ["a"], self.now)); self.assertEqual(s.update(1, tags=["b"])["tags"], ["b"]); self.assertEqual([x["id"] for x in s.search("hello", ["b"])], [1]); self.assertEqual(s.tags(), [("b", 1)]); self.assertEqual(s.delete(1)["title"], "Hello")
    def test_r01_ids_are_never_reused_even_for_the_newest_note(self): s = self.store(); s.add("a"); s.add("b"); s.delete(2); s2 = self.store(); self.assertEqual(s2.add("c")["id"], 3)
    def test_r01_tag_order_is_preserved(self): s = self.store(); s.add("A", tags=["zeta", "alpha", "mid"]); self.assertEqual(self.store().get(1)["tags"], ["zeta", "alpha", "mid"])
    def test_r01_a_failing_operation_changes_nothing(self):
        s = self.store(); s.add("Keep", "b", ["t"])
        for call in (lambda: s.update(1, title="new", tags=["bad tag"]), lambda: s.update(9, title="x"), lambda: s.add("", "x"), lambda: s.delete(9)):
            with self.assertRaises((ValidationError, NotFound)): call()
        self.assertEqual((s.get(1)["title"], s.get(1)["tags"], len(s.list())), ("Keep", ["t"], 1)); self.assertEqual(s.add("next")["id"], 2)
    def test_r01_cascading_delete_leaves_no_orphan_tags(self): s = self.store(); s.add("A", tags=["x"]); s.delete(1); self.assertEqual(s.tags(), [])
    def test_r01_threads_do_not_lose_updates(self):
        s = self.store(); errs = []
        def work(n):
            st = self.store()
            try:
                for i in range(10): st.add(f"t{n}-{i}", tags=[f"w{n}"])
            except Exception as e: errs.append(e)
        ts = [threading.Thread(target=work, args=(n,)) for n in range(6)]; [t.start() for t in ts]; [t.join(120) for t in ts]; self.assertEqual(errs, []); self.assertEqual(sorted(n["id"] for n in s.list()), list(range(1, 61))); self.assertEqual(len(s.tags()), 6)
    def test_r01_processes_do_not_lose_updates(self):
        code = "import sys, os; sys.path.insert(0, os.getcwd()); import notes\ns = notes.NoteStore(sys.argv[1])\nfor i in range(10): s.add('p%s-%d' % (sys.argv[2], i))\n"
        ps = [subprocess.Popen([sys.executable, "-c", code, self.path, str(k)], cwd=WS) for k in range(4)]; [p.wait(120) for p in ps]; self.assertEqual(sorted(n["id"] for n in self.store().list()), list(range(1, 41)))
    def test_r01_no_leftover_files(self): s = self.store(); s.add("A"); s.update(1, title="B"); s.delete(1); s.close(); self.assertEqual(os.listdir(self.dir), ["n.json"])
    def test_r01_one_store_object_may_be_shared_between_threads(self):
        s = self.store(); errs = []
        def work(n):
            try:
                for i in range(10): s.add(f"t{n}-{i}")
            except Exception as e: errs.append(e)
        ts = [threading.Thread(target=work, args=(n,)) for n in range(4)]; [t.start() for t in ts]; [t.join(120) for t in ts]; self.assertEqual((errs, len(s.list())), ([], 40))

class TestMigration(Base):
    def test_r02_a_json_store_is_converted_on_open(self):
        self.legacy(); s = self.store(); self.assertEqual([(n["id"], n["title"], n["body"], n["tags"], n["created"], n["updated"]) for n in s.list()], [(2, "Old one", "b1", ["x", "y"], "2024-01-01T00:00:00", "2024-01-02T00:00:00"), (5, "Old two", "", [], "2024-01-03T00:00:00", "2024-01-03T00:00:00")]); self.assertEqual(open(self.path, "rb").read(15), b"SQLite format 3")
    def test_r02_the_next_id_continues_where_the_file_left_off(self): self.legacy(); self.assertEqual(self.store().add("new")["id"], 9)
    def test_r02_the_original_is_kept_as_a_backup(self): self.legacy(); original = open(self.path).read(); self.store(); self.assertEqual(open(self.path + ".json.bak").read(), original)
    def test_r02_reopening_converts_nothing_again(self):
        self.legacy(); self.store(); os.remove(self.path + ".json.bak"); s = self.store(); self.assertFalse(os.path.exists(self.path + ".json.bak")); self.assertEqual(len(s.list()), 2)
    def test_r02_a_stage_1_file_without_tags(self): self.legacy({"next_id": 2, "notes": [{"id": 1, "title": "T", "body": "b", "created": "c", "updated": "u"}]}); self.assertEqual(self.store().get(1)["tags"], [])
    def test_r02_an_empty_json_store(self): self.legacy({"next_id": 4, "notes": []}); s = self.store(); self.assertEqual((s.list(), s.add("a")["id"]), ([], 4))
    def test_r02_converted_data_is_fully_usable(self): self.legacy(); s = self.store(); s.update(2, tags=["z"]); s.delete(5); self.assertEqual(s.tags(), [("z", 1)]); self.assertEqual([n["id"] for n in self.store().list()], [2])
    def test_r02_garbage_is_a_store_error_and_untouched(self):
        for junk in (b"{nope", b"[]", b"", b'{"next_id": 1}', b'{"next_id": "x", "notes": []}', b"\x00\x01\x02 binary", b'{"next_id": 1, "notes": [{"id": 1}]}'):
            open(self.path, "wb").write(junk)
            with self.assertRaises(StoreError) as cm: self.store()
            self.assertEqual(str(cm.exception), f"data file is corrupt: {self.path}"); self.assertEqual(open(self.path, "rb").read(), junk); self.assertFalse(os.path.exists(self.path + ".json.bak"))
    def test_r02_a_foreign_sqlite_database_is_a_store_error(self):
        c = sqlite3.connect(self.path); c.execute("CREATE TABLE other(x)"); c.commit(); c.close(); before = open(self.path, "rb").read()
        with self.assertRaises(StoreError): self.store()
        self.assertEqual(open(self.path, "rb").read(), before)
    def test_r02_a_missing_path_creates_an_empty_database(self): s = self.store(); self.assertEqual((s.list(), os.path.exists(self.path)), ([], True)); self.assertEqual(s.add("a")["id"], 1)
    def test_r02_no_temporary_files_remain_after_a_migration(self): self.legacy(); self.store().close(); self.assertEqual(sorted(os.listdir(self.dir)), ["n.json", "n.json.json.bak"])
    def test_r02_the_cli_migrates_too(self):
        self.legacy(); env = dict(os.environ, NOTES_FILE=self.path, PYTHONPATH=WS); p = subprocess.run([sys.executable, "-m", "notes", "list"], capture_output=True, text=True, env=env, cwd=self.dir, timeout=60); self.assertEqual((p.returncode, p.stdout), (0, "  2  Old one  #x #y\n  5  Old two\n"))

class TestExportStatsCli(Base):
    def test_r03_export_json_in_the_old_format(self):
        s = self.store(); s.add("A", "b", ["x"]); s.add("B"); s.delete(2); out = os.path.join(self.dir, "e.json"); self.assertEqual(s.export_json(out), 1); d = json.load(open(out)); self.assertEqual((d["next_id"], [(n["id"], n["title"], n["tags"]) for n in d["notes"]]), (3, [(1, "A", ["x"])])); self.assertEqual(sorted(os.listdir(self.dir)), ["e.json", "n.json"])
    def test_r03_an_export_can_be_opened_again(self):
        s = self.store(); s.add("A", "b", ["x"]); out = os.path.join(self.dir, "e.json"); s.export_json(out); t = self.store(out); self.assertEqual((t.get(1)["title"], t.get(1)["tags"], t.add("n")["id"]), ("A", ["x"], 2))
    def test_r03_an_empty_export(self): out = os.path.join(self.dir, "e.json"); self.assertEqual(self.store().export_json(out), 0); self.assertEqual(json.load(open(out))["notes"], [])
    def run_cli(self, *argv):
        env = dict(os.environ, NOTES_FILE=self.path, NOTES_NOW=self.now, PYTHONPATH=WS); p = subprocess.run([sys.executable, "-m", "notes", *argv], capture_output=True, text=True, env=env, cwd=self.dir, timeout=60); return p.returncode, p.stdout, p.stderr
    def test_r04_stats(self):
        self.assertEqual(self.run_cli("stats")[1], "Notes: 0\nTags: 0\n"); self.run_cli("add", "A", "--tag", "x", "--tag", "y"); self.run_cli("add", "B", "--tag", "x"); self.assertEqual(self.run_cli("stats")[1], "Notes: 2\nTags: 2\n")
    def test_r04_the_old_commands_are_unchanged(self):
        self.assertEqual(self.run_cli("add", "Hello", "--body", "W", "--tag", "t")[1], "Added note 1\n"); self.assertEqual(self.run_cli("list")[1], "  1  Hello  #t\n"); self.assertEqual(self.run_cli("show", "1")[1], "Hello\nW\n"); self.assertEqual(self.run_cli("tags")[1], "t: 1\n"); self.assertEqual(self.run_cli("search", "hello")[1], "  1  Hello  #t\n")
        self.assertEqual(self.run_cli("show", "9")[:1], (1,)); self.assertEqual(self.run_cli("add", "")[2], "Error: title must not be empty\n")
