import io, json, os, subprocess, sys, tempfile, unittest
import notes
from notes import NoteStore, NotFound, NotesError, StoreError, ValidationError

WS = os.getcwd()

class Base(unittest.TestCase):
    def setUp(self): self.dir = tempfile.mkdtemp(prefix="g1-"); self.path = os.path.join(self.dir, "n.json"); self.now = "2024-03-01T09:00:00"
    def tearDown(self):
        import shutil; shutil.rmtree(self.dir, ignore_errors=True)
    def store(self): return NoteStore(self.path, clock=lambda: self.now)

class TestStore(Base):
    def test_r01_add_returns_the_note(self):
        n = self.store().add("Hello", "World")
        for k, v in {"id": 1, "title": "Hello", "body": "World", "created": self.now, "updated": self.now}.items(): self.assertEqual(n[k], v, k)        # later stages add more keys: only these are fixed now
    def test_r01_body_defaults_to_empty(self): self.assertEqual(self.store().add("T")["body"], "")
    def test_r01_get_and_list(self): s = self.store(); s.add("A"); s.add("B"); self.assertEqual(s.get(2)["title"], "B"); self.assertEqual([n["id"] for n in s.list()], [1, 2])
    def test_r01_update_changes_only_given_fields(self):
        s = self.store(); s.add("Old", "body"); self.now = "2024-03-02T09:00:00"; n = s.update(1, title="New"); self.assertEqual((n["title"], n["body"], n["created"], n["updated"]), ("New", "body", "2024-03-01T09:00:00", "2024-03-02T09:00:00")); self.assertEqual(s.update(1, body="")["body"], "")
    def test_r01_delete_returns_the_note(self): s = self.store(); s.add("A"); n = s.delete(1); self.assertEqual(n["title"], "A"); self.assertEqual(s.list(), [])
    def test_r01_returned_dicts_are_copies(self): s = self.store(); n = s.add("A"); n["title"] = "hacked"; s.get(1)["title"] = "x"; s.list()[0]["title"] = "y"; self.assertEqual(s.get(1)["title"], "A")
    def test_r01_default_clock_uses_notes_now(self):
        os.environ["NOTES_NOW"] = "2030-01-01T00:00:00"
        try: self.assertEqual(NoteStore(self.path).add("A")["created"], "2030-01-01T00:00:00")
        finally: del os.environ["NOTES_NOW"]
    def test_r01_default_clock_without_the_variable_is_a_timestamp(self):
        import re; os.environ.pop("NOTES_NOW", None); self.assertRegex(NoteStore(self.path).add("A")["created"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
    def test_r02_ids_are_never_reused(self):
        s = self.store(); s.add("a"); s.add("b"); s.delete(2); self.assertEqual(s.add("c")["id"], 3); s.delete(1); s.delete(3); self.assertEqual(s.add("d")["id"], 4); self.assertEqual(self.store().add("e")["id"], 5)
    def test_r02_created_is_set_once(self): s = self.store(); s.add("A"); n = s.get(1); self.assertEqual(n["created"], n["updated"]); self.now = "2024-04-01T00:00:00"; s.update(1, body="x"); self.assertEqual(s.get(1)["created"], "2024-03-01T09:00:00")
    def test_r03_title_rules(self):
        s = self.store()
        for bad in ("", "   ", "x" * 101, "two\nlines", None, 5, ["a"]):
            with self.assertRaises(ValidationError, msg=repr(bad)): s.add(bad)
        self.assertEqual(s.add("  padded  ")["title"], "padded"); self.assertEqual(len(s.add("x" * 100)["title"]), 100); self.assertEqual(len(s.list()), 2)
    def test_r03_body_rules(self):
        s = self.store()
        for bad in ("x" * 5001, None, 5):
            with self.assertRaises(ValidationError): s.add("T", bad)
        self.assertEqual(s.add("T", " keep  spaces\n")["body"], " keep  spaces\n"); self.assertEqual(len(s.add("T", "y" * 5000)["body"]), 5000)
    def test_r03_update_validates_and_changes_nothing(self):
        s = self.store(); s.add("Keep", "me")
        with self.assertRaises(ValidationError): s.update(1, title="", body="changed")
        self.assertEqual(s.get(1)["body"], "me")
    def test_r03_error_classes(self):
        self.assertTrue(issubclass(ValidationError, NotesError) and issubclass(ValidationError, ValueError)); self.assertTrue(issubclass(NotFound, NotesError) and issubclass(NotFound, KeyError)); self.assertTrue(issubclass(StoreError, NotesError)); self.assertTrue(issubclass(NotesError, Exception))
    def test_r03_missing_ids(self):
        s = self.store()
        for call in (lambda: s.get(1), lambda: s.update(1, title="x"), lambda: s.delete(1)):
            with self.assertRaises(NotFound) as cm: call()
            self.assertEqual(cm.exception.args[0], 1)
    def test_r04_persistence(self): s = self.store(); s.add("A", "a"); s.add("B"); s.update(1, body="a2"); s.delete(2); s2 = self.store(); self.assertEqual(s2.list(), s.list()); self.assertEqual(s2.add("C")["id"], 3)
    def test_r04_a_missing_file_is_an_empty_store(self): s = self.store(); self.assertEqual(s.list(), []); self.assertEqual(s.add("A")["id"], 1)
    def test_r04_no_temporary_files_remain(self): s = self.store(); s.add("A"); s.update(1, title="B"); s.delete(1); self.assertEqual(os.listdir(self.dir), ["n.json"])
    def test_r04_a_corrupt_file_raises_store_error_and_is_untouched(self):
        for junk in ("{nope", "[]", "", '{"next_id": 1}', '{"next_id": "x", "notes": []}'):
            open(self.path, "w").write(junk)
            with self.assertRaises(StoreError) as cm: self.store()
            self.assertEqual(str(cm.exception), f"data file is corrupt: {self.path}"); self.assertEqual(open(self.path).read(), junk)

class Cli(Base):
    def run_cli(self, *argv):
        env = dict(os.environ, NOTES_FILE=self.path, NOTES_NOW=self.now, PYTHONPATH=WS); p = subprocess.run([sys.executable, "-m", "notes", *argv], capture_output=True, text=True, env=env, cwd=self.dir, timeout=60)
        self.assertNotIn("No module named notes", p.stderr); return p.returncode, p.stdout, p.stderr
    def ok(self, *argv): rc, out, err = self.run_cli(*argv); self.assertEqual(rc, 0, (argv, err)); return out

class TestCli(Cli):
    def test_r05_add_and_list(self): self.assertEqual(self.ok("add", "Hello"), "Added note 1\n"); self.assertEqual(self.ok("add", "Second", "--body", "text"), "Added note 2\n"); self.assertEqual(self.ok("list"), "  1  Hello\n  2  Second\n")
    def test_r05_list_empty(self): self.assertEqual(self.ok("list"), "No notes.\n")
    def test_r05_show(self): self.ok("add", "Title", "--body", "line1\nline2"); self.assertEqual(self.ok("show", "1"), "Title\nline1\nline2\n")
    def test_r05_show_without_a_body(self): self.ok("add", "Title"); self.assertEqual(self.ok("show", "1"), "Title\n\n")
    def test_r05_delete(self): self.ok("add", "A"); self.assertEqual(self.ok("delete", "1"), "Deleted note 1\n"); self.assertEqual(self.ok("list"), "No notes.\n")
    def test_r05_edit(self): self.ok("add", "Old", "--body", "b"); self.assertEqual(self.ok("edit", "1", "--title", "New"), "Updated note 1\n"); self.assertEqual(self.ok("show", "1"), "New\nb\n"); self.ok("edit", "1", "--body", "c"); self.assertEqual(self.ok("show", "1"), "New\nc\n")
    def test_r05_ids_above_nine_are_aligned(self):
        for i in range(10): self.ok("add", f"n{i}")
        self.assertTrue(self.ok("list").splitlines()[-1].startswith(" 10  n9"))
    def test_r05_main_returns_the_status(self):
        os.environ["NOTES_FILE"] = self.path
        try: buf = io.StringIO(); self.assertEqual(notes.main(["add", "x"], buf), 0); self.assertEqual(buf.getvalue(), "Added note 1\n")
        finally: del os.environ["NOTES_FILE"]
    def test_r05_default_file_is_notes_json_in_the_cwd(self):
        env = {k: v for k, v in os.environ.items() if k != "NOTES_FILE"}; env["PYTHONPATH"] = WS; p = subprocess.run([sys.executable, "-m", "notes", "add", "x"], cwd=self.dir, env=env, capture_output=True, text=True, timeout=60); self.assertEqual(p.returncode, 0); self.assertTrue(os.path.exists(os.path.join(self.dir, "notes.json")))
    def test_r06_exit_statuses(self):
        rc, out, err = self.run_cli("show", "9"); self.assertEqual((rc, err), (1, "Error: no note with id 9\n")); self.assertEqual(self.run_cli("delete", "9")[0], 1); self.assertEqual(self.run_cli("edit", "9", "--title", "x")[0], 1)
        rc, out, err = self.run_cli("add", "  "); self.assertEqual((rc, err), (2, "Error: title must not be empty\n")); self.assertEqual(self.run_cli("show", "abc")[0], 2); self.assertEqual(self.run_cli("frobnicate")[0], 2); self.assertEqual(self.run_cli()[0], 2)
    def test_r06_usage_goes_to_stderr(self): rc, out, err = self.run_cli(); self.assertEqual(out, ""); self.assertIn("usage", err.lower())
    def test_r06_nothing_to_edit(self): self.ok("add", "A"); rc, out, err = self.run_cli("edit", "1"); self.assertEqual((rc, err), (2, "Error: nothing to edit\n"))
    def test_r06_a_corrupt_file(self): open(self.path, "w").write("{nope"); rc, out, err = self.run_cli("list"); self.assertEqual((rc, err), (2, f"Error: data file is corrupt: {self.path}\n")); self.assertEqual(open(self.path).read(), "{nope")
    def test_r06_the_delete_of_a_missing_note_changes_nothing(self): self.ok("add", "A"); before = open(self.path, "rb").read(); self.run_cli("delete", "5"); self.assertEqual(open(self.path, "rb").read(), before)
