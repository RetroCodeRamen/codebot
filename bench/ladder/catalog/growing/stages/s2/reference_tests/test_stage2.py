import os, subprocess, sys, tempfile, unittest
from notes import NoteStore, ValidationError

WS = os.getcwd()

class Base(unittest.TestCase):
    def setUp(self): self.dir = tempfile.mkdtemp(prefix="g2-"); self.path = os.path.join(self.dir, "n.json"); self.now = "2024-03-01T09:00:00"
    def tearDown(self):
        import shutil; shutil.rmtree(self.dir, ignore_errors=True)
    def store(self): return NoteStore(self.path, clock=lambda: self.now)

class TestTags(Base):
    def test_r01_tags_default_to_empty(self): self.assertEqual(self.store().add("A")["tags"], [])
    def test_r01_tags_are_normalised(self): self.assertEqual(self.store().add("A", tags=[" Work ", "work", "HOME", "a_b-c"])["tags"], ["work", "home", "a_b-c"])
    def test_r01_tags_accept_tuples_and_generators(self): s = self.store(); self.assertEqual(s.add("A", tags=("x", "y"))["tags"], ["x", "y"]); self.assertEqual(s.add("B", tags=(t for t in "ab"))["tags"], ["a", "b"])
    def test_r01_invalid_tags(self):
        s = self.store()
        for bad in ("abc", [""], ["-x"], ["a b"], ["x" * 21], ["ä"], ["a", 5], 5, [f"t{i}" for i in range(11)]):
            with self.assertRaises(ValidationError, msg=repr(bad)): s.add("A", tags=bad)
        self.assertEqual(s.list(), [])
    def test_r01_boundaries(self): s = self.store(); self.assertEqual(len(s.add("A", tags=[f"t{i}" for i in range(10)])["tags"]), 10); self.assertEqual(s.add("B", tags=["x" * 20])["tags"], ["x" * 20]); self.assertEqual(len(s.add("C", tags=[f"t{i % 10}" for i in range(30)])["tags"]), 10)
    def test_r01_update_replaces_the_tags(self): s = self.store(); s.add("A", tags=["a", "b"]); self.assertEqual(s.update(1, tags=["c"])["tags"], ["c"]); self.assertEqual(s.update(1, title="T")["tags"], ["c"]); self.assertEqual(s.update(1, tags=[])["tags"], [])
    def test_r01_a_failed_update_changes_nothing(self):
        s = self.store(); s.add("A", tags=["a"])
        with self.assertRaises(ValidationError): s.update(1, title="new", tags=["bad tag"])
        self.assertEqual((s.get(1)["title"], s.get(1)["tags"]), ("A", ["a"]))
    def test_r01_tags_persist(self): s = self.store(); s.add("A", tags=["x"]); self.assertEqual(self.store().get(1)["tags"], ["x"])
    def test_r01_stage_1_data_files_still_load(self):
        import json; json.dump({"next_id": 3, "notes": [{"id": 2, "title": "Old", "body": "b", "created": "2024-01-01T00:00:00", "updated": "2024-01-01T00:00:00"}]}, open(self.path, "w")); s = self.store(); self.assertEqual((s.get(2)["tags"], s.add("New")["id"]), ([], 3))
    def test_r01_stage_1_behaviour_is_unchanged(self): s = self.store(); n = s.add("Hello", "World"); self.assertEqual((n["id"], n["title"], n["body"]), (1, "Hello", "World")); s.update(1, body="x"); s.delete(1); self.assertEqual(s.add("again")["id"], 2)
    def test_r02_search_words(self):
        s = self.store(); s.add("Shopping list", "milk and eggs"); s.add("Meeting notes", "discuss the MILK budget"); s.add("Other")
        self.assertEqual([n["id"] for n in s.search("milk")], [1, 2]); self.assertEqual([n["id"] for n in s.search("MILK budget")], [2]); self.assertEqual([n["id"] for n in s.search("list eggs")], [1]); self.assertEqual(s.search("nothing"), [])
    def test_r02_search_tags(self):
        s = self.store(); s.add("A", tags=["x", "y"]); s.add("B", tags=["x"]); s.add("C"); self.assertEqual([n["id"] for n in s.search(tags=["x"])], [1, 2]); self.assertEqual([n["id"] for n in s.search("", ["X", "y"])], [1]); self.assertEqual(s.search("", ["none"]), [])
    def test_r02_words_and_tags_combine(self): s = self.store(); s.add("Milk", tags=["home"]); s.add("Milk run", tags=["work"]); self.assertEqual([n["id"] for n in s.search("milk", ["work"])], [2])
    def test_r02_an_empty_query_matches_everything_in_id_order(self): s = self.store(); s.add("B"); s.add("A"); self.assertEqual([n["id"] for n in s.search()], [1, 2]); self.assertEqual([n["id"] for n in s.search("   ")], [1, 2])
    def test_r02_invalid_search_tags(self):
        with self.assertRaises(ValidationError): self.store().search("", ["bad tag"])
    def test_r02_search_returns_copies(self): s = self.store(); s.add("A"); s.search()[0]["title"] = "x"; self.assertEqual(s.get(1)["title"], "A")
    def test_r02_words_match_inside_words_and_across_title_and_body(self): s = self.store(); s.add("Grocer", "bread"); self.assertEqual(len(s.search("groc")), 1); self.assertEqual(len(s.search("grocer bread")), 1)
    def test_r03_tag_overview(self):
        s = self.store(); self.assertEqual(s.tags(), []); s.add("A", tags=["b", "a"]); s.add("B", tags=["b"]); self.assertEqual(s.tags(), [("a", 1), ("b", 2)]); s.delete(2); self.assertEqual(s.tags(), [("a", 1), ("b", 1)])

class TestCli(Base):
    def run_cli(self, *argv):
        env = dict(os.environ, NOTES_FILE=self.path, NOTES_NOW=self.now, PYTHONPATH=WS); p = subprocess.run([sys.executable, "-m", "notes", *argv], capture_output=True, text=True, env=env, cwd=self.dir, timeout=60); return p.returncode, p.stdout, p.stderr
    def ok(self, *argv): rc, out, err = self.run_cli(*argv); self.assertEqual(rc, 0, (argv, err)); return out
    def test_r04_add_with_tags_and_list(self): self.ok("add", "Shopping", "--tag", "Home", "--tag", "errand"); self.ok("add", "Plain"); self.assertEqual(self.ok("list"), "  1  Shopping  #home #errand\n  2  Plain\n")
    def test_r04_list_by_tag(self): self.ok("add", "A", "--tag", "x", "--tag", "y"); self.ok("add", "B", "--tag", "x"); self.assertEqual(self.ok("list", "--tag", "x"), "  1  A  #x #y\n  2  B  #x\n"); self.assertEqual(self.ok("list", "--tag", "x", "--tag", "y"), "  1  A  #x #y\n"); self.assertEqual(self.ok("list", "--tag", "zzz"), "No notes.\n")
    def test_r04_search(self): self.ok("add", "Milk run", "--body", "buy milk", "--tag", "home"); self.ok("add", "Other"); self.assertEqual(self.ok("search", "milk"), "  1  Milk run  #home\n"); self.assertEqual(self.ok("search", "milk", "--tag", "home"), "  1  Milk run  #home\n"); self.assertEqual(self.ok("search", "nope"), "No matches.\n")
    def test_r04_search_with_several_words(self): self.ok("add", "Big red apple"); self.assertEqual(self.ok("search", "red", "apple"), "  1  Big red apple\n")
    def test_r04_tags_command(self): self.assertEqual(self.ok("tags"), "No tags.\n"); self.ok("add", "A", "--tag", "b", "--tag", "a"); self.ok("add", "B", "--tag", "b"); self.assertEqual(self.ok("tags"), "a: 1\nb: 2\n")
    def test_r04_edit_replaces_tags(self): self.ok("add", "A", "--tag", "a"); self.assertEqual(self.ok("edit", "1", "--tag", "x", "--tag", "y"), "Updated note 1\n"); self.assertEqual(self.ok("list"), "  1  A  #x #y\n")
    def test_r04_invalid_tags_are_a_status_2_error(self): rc, out, err = self.run_cli("add", "A", "--tag", "bad tag"); self.assertEqual((rc, err), (2, "Error: invalid tag 'bad tag'\n")); self.assertEqual(self.ok("list"), "No notes.\n")
    def test_r04_stage_1_commands_still_work(self): self.assertEqual(self.ok("add", "Hello", "--body", "World"), "Added note 1\n"); self.assertEqual(self.ok("show", "1"), "Hello\nWorld\n"); self.assertEqual(self.ok("list"), "  1  Hello\n"); self.assertEqual(self.ok("delete", "1"), "Deleted note 1\n"); self.assertEqual(self.run_cli("show", "1")[0], 1)
    def test_r04_edit_without_options_still_fails(self): self.ok("add", "A"); self.assertEqual(self.run_cli("edit", "1")[:1], (2,))
