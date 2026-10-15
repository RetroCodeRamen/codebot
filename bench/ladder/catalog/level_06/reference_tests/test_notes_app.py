import io, json, os, subprocess, sys, tempfile, unittest
from notes.models import Note
from notes.storage import NoteStore
from notes import cli, transfer
from notes.transfer import TransferError, export_json, export_markdown, import_json

WS = os.getcwd()
T = "2024-01-01T10:00:00"

class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="notes-test-"); self.path = os.path.join(self.dir, "n.json"); self.out = os.path.join(self.dir, "out")
    def tearDown(self):
        import shutil; shutil.rmtree(self.dir, ignore_errors=True)
    def store(self, path=None): return NoteStore(path or self.path, clock=lambda: T)
    def filled(self):
        s = self.store(); s.add("Alpha", "first body", ["x", "y"]); s.add("Beta", "", [], True); s.add("Gamma", "third", ["x"]); return s
    def read(self, p=None):
        with open(p or self.out) as f: return f.read()

class TestExport(Base):
    def test_r05_json_export_structure(self):
        s = self.filled(); self.assertEqual(export_json(s, self.out), 3); d = json.loads(self.read()); self.assertEqual(d["version"], 1); self.assertEqual([n["id"] for n in d["notes"]], [1, 2, 3]); self.assertEqual(d["notes"][0], s.get(1).to_dict())
    def test_r05_json_export_of_an_empty_store(self): self.assertEqual(export_json(self.store(), self.out), 0); self.assertEqual(json.loads(self.read()), {"version": 1, "notes": []})
    def test_r05_markdown_exact_format_for_one_note(self):
        s = self.store(); s.add("T", "B", ["x"]); self.assertEqual(export_markdown(s, self.out), 1); self.assertEqual(self.read(), "# T\n\nTags: x\n\nB\n\n---\n")
    def test_r05_markdown_omits_empty_tags_and_body(self): s = self.store(); s.add("T"); export_markdown(s, self.out); self.assertEqual(self.read(), "# T\n\n---\n")
    def test_r05_markdown_pinned_first_then_by_id(self):
        s = self.filled(); export_markdown(s, self.out); titles = [l[2:] for l in self.read().splitlines() if l.startswith("# ")]; self.assertEqual(titles, ["Beta", "Alpha", "Gamma"])
    def test_r05_markdown_blocks_are_separated_by_one_empty_line(self):
        s = self.store(); s.add("A"); s.add("B"); export_markdown(s, self.out); self.assertEqual(self.read(), "# A\n\n---\n\n# B\n\n---\n")
    def test_r05_markdown_with_tags_only(self): s = self.store(); s.add("T", "", ["a", "b"]); export_markdown(s, self.out); self.assertEqual(self.read(), "# T\n\nTags: a, b\n\n---\n")
    def test_r05_multi_line_body_is_kept(self): s = self.store(); s.add("T", "l1\nl2"); export_markdown(s, self.out); self.assertEqual(self.read(), "# T\n\nl1\nl2\n\n---\n")
    def test_r05_export_overwrites_atomically_without_leftovers(self):
        s = self.filled(); export_json(s, self.out); export_json(s, self.out); self.assertEqual(sorted(os.listdir(self.dir)), ["n.json", "out"])
    def test_r05_empty_markdown_export(self): self.assertEqual(export_markdown(self.store(), self.out), 0); self.assertEqual(self.read(), "")
    def test_r05_export_error_class(self): self.assertTrue(issubclass(TransferError, Exception))
    def test_r05_markdown_title_with_unicode(self): s = self.store(); s.add("Café ✓", "ünï"); export_markdown(s, self.out); self.assertEqual(self.read(), "# Café ✓\n\nünï\n\n---\n")

class TestImport(Base):
    def exported(self, notes):
        p = os.path.join(self.dir, "in.json"); json.dump({"version": 1, "notes": [n.to_dict() if isinstance(n, Note) else n for n in notes]}, open(p, "w")); return p
    def test_r06_round_trip_into_an_empty_store(self):
        src = self.filled(); p = os.path.join(self.dir, "x.json"); export_json(src, p); dst = self.store(os.path.join(self.dir, "other.json"))
        self.assertEqual(import_json(dst, p), {"added": 3, "skipped": 0, "overwritten": 0}); self.assertEqual([n.to_dict() for n in dst.all()], [n.to_dict() for n in src.all()])
    def test_r06_ids_and_timestamps_are_kept(self):
        p = self.exported([Note(7, "Seven", "b", ["t"], "c7", "u7", True)]); s = self.store(); import_json(s, p); n = s.get(7); self.assertEqual((n.created, n.updated, n.pinned, n.tags), ("c7", "u7", True, ["t"])); self.assertEqual(s.add("next").id, 8)
    def test_r06_skip_on_conflict(self):
        s = self.filled(); p = self.exported([Note(1, "Changed"), Note(9, "New")]); self.assertEqual(import_json(s, p), {"added": 1, "skipped": 1, "overwritten": 0}); self.assertEqual(s.get(1).title, "Alpha"); self.assertEqual(s.get(9).title, "New")
    def test_r06_overwrite_on_conflict(self):
        s = self.filled(); p = self.exported([Note(1, "Changed", "nb", ["z"], pinned=True)]); self.assertEqual(import_json(s, p, "overwrite"), {"added": 0, "skipped": 0, "overwritten": 1}); n = s.get(1); self.assertEqual((n.title, n.body, n.tags, n.pinned), ("Changed", "nb", ["z"], True)); self.assertEqual(len(s), 3)
    def test_r06_overwrite_keeps_the_original_creation_time_and_touches_updated(self):
        s = self.filled(); s.clock = lambda: "2025-05-05T05:05:05"; import_json(s, self.exported([Note(1, "Changed", created="old")]), "overwrite"); n = s.get(1); self.assertEqual((n.created, n.updated), (T, "2025-05-05T05:05:05"))
    def test_r06_duplicate_on_conflict_gets_a_new_id(self):
        s = self.filled(); p = self.exported([Note(1, "Alpha again")]); self.assertEqual(import_json(s, p, "duplicate"), {"added": 1, "skipped": 0, "overwritten": 0}); self.assertEqual(len(s), 4); self.assertEqual(s.get(4).title, "Alpha again"); self.assertEqual(s.get(1).title, "Alpha")
    def test_r06_default_mode_is_skip(self): s = self.filled(); self.assertEqual(import_json(s, self.exported([Note(2, "x")]))["skipped"], 1)
    def test_r06_invalid_mode(self):
        with self.assertRaises(ValueError): import_json(self.store(), self.exported([]), "merge")
    def test_r06_not_a_notes_file(self):
        for junk in ("{nope", "[]", '{"version": 1}', '{"version": 2, "notes": []}', '{"notes": []}', '{"version": 1, "notes": "x"}', ""):
            p = os.path.join(self.dir, "bad.json"); open(p, "w").write(junk)
            with self.assertRaises(TransferError) as cm: import_json(self.store(), p)
            self.assertEqual(str(cm.exception), "not a valid notes export file", junk)
    def test_r06_a_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError): import_json(self.store(), os.path.join(self.dir, "nope.json"))
    def test_r06_an_invalid_note_aborts_everything_and_names_its_position(self):
        s = self.filled(); before = open(self.path).read(); p = self.exported([Note(10, "ok"), {"id": 11, "title": "  "}, Note(12, "also ok")])
        with self.assertRaises(TransferError) as cm: import_json(s, p)
        self.assertEqual(str(cm.exception), "note 2: title must not be empty"); self.assertEqual(open(self.path).read(), before); self.assertEqual(len(s), 3)
    def test_r06_validation_messages_are_reused(self):
        for rec, msg in (({"id": 1, "title": "t", "tags": ["bad tag"]}, "note 1: invalid tag 'bad tag'"), ({"id": 1, "title": "t", "body": 5}, "note 1: body must be a string"), ({"id": 1, "title": 5}, "note 1: title must be a string")):
            with self.assertRaises(TransferError) as cm: import_json(self.store(os.path.join(self.dir, "z.json")), self.exported([rec]))
            self.assertEqual(str(cm.exception), msg)
    def test_r06_the_id_must_be_an_integer(self):
        for rec in ({"title": "t"}, {"id": "1", "title": "t"}, {"id": True, "title": "t"}, {"id": 1.5, "title": "t"}):
            with self.assertRaises(TransferError) as cm: import_json(self.store(os.path.join(self.dir, "y.json")), self.exported([rec]))
            self.assertTrue(str(cm.exception).startswith("note 1:"))
    def test_r06_missing_optional_fields_take_defaults(self): s = self.store(); import_json(s, self.exported([{"id": 3, "title": "Bare"}])); n = s.get(3); self.assertEqual((n.body, n.tags, n.pinned), ("", [], False))
    def test_r06_tags_are_cleaned_on_import(self): s = self.store(); import_json(s, self.exported([{"id": 1, "title": "t", "tags": [" A ", "a", "B"]}])); self.assertEqual(s.get(1).tags, ["a", "b"])
    def test_r06_the_title_is_stripped_on_import(self): s = self.store(); import_json(s, self.exported([{"id": 1, "title": "  padded  "}])); self.assertEqual(s.get(1).title, "padded")
    def test_r06_importing_an_empty_list(self): self.assertEqual(import_json(self.store(), self.exported([])), {"added": 0, "skipped": 0, "overwritten": 0})
    def test_r06_importing_twice_with_skip_adds_nothing_the_second_time(self):
        s = self.store(); p = self.exported([Note(1, "a"), Note(2, "b")]); import_json(s, p); self.assertEqual(import_json(s, p), {"added": 0, "skipped": 2, "overwritten": 0})

class Cli(Base):
    def setUp(self):
        super().setUp(); self.saved = {k: os.environ.get(k) for k in ("NOTES_FILE", "NOTES_NOW")}; os.environ["NOTES_FILE"] = self.path; os.environ["NOTES_NOW"] = T
    def tearDown(self):
        for k, v in self.saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        super().tearDown()
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO(); old = sys.stderr; sys.stderr = err
        try: rc = cli.main(list(argv), out=out)
        finally: sys.stderr = old
        return rc, out.getvalue(), err.getvalue()
    def ok(self, *argv):
        rc, out, err = self.run_cli(*argv); self.assertEqual(rc, 0, (argv, err)); return out

class TestCliCore(Cli):
    def test_r07_add_and_list(self):
        self.assertEqual(self.ok("add", "First", "--body", "hello", "--tag", "A", "--tag", "b"), "Added note 1\n"); self.assertEqual(self.ok("add", "Second", "--pin"), "Added note 2\n")
        self.assertEqual(self.ok("list"), "  2 * Second\n  1   First #a #b\n")
    def test_r07_list_empty(self): self.assertEqual(self.ok("list"), "No notes.\n")
    def test_r07_list_filters(self):
        self.ok("add", "A", "--tag", "x"); self.ok("add", "B", "--tag", "x", "--tag", "y", "--pin"); self.ok("add", "C")
        self.assertEqual(self.ok("list", "--tag", "x"), "  2 * B #x #y\n  1   A #x\n"); self.assertEqual(self.ok("list", "--tag", "x", "--tag", "y"), "  2 * B #x #y\n"); self.assertEqual(self.ok("list", "--pinned"), "  2 * B #x #y\n"); self.assertEqual(self.ok("list", "--tag", "zzz"), "No notes.\n")
    def test_r07_show(self):
        self.ok("add", "Title", "--body", "line1\nline2", "--tag", "t", "--pin"); self.assertEqual(self.ok("show", "1"), f"Note 1: Title\nCreated: {T}\nUpdated: {T}\nTags: t\nPinned\n\nline1\nline2\n")
    def test_r07_show_without_tags_or_pin(self): self.ok("add", "Plain"); self.assertEqual(self.ok("show", "1"), f"Note 1: Plain\nCreated: {T}\nUpdated: {T}\n\n\n")
    def test_r07_edit_changes_only_what_is_given(self):
        self.ok("add", "Old", "--body", "b", "--tag", "x"); self.assertEqual(self.ok("edit", "1", "--title", "New"), "Updated note 1\n"); n = self.store().get(1); self.assertEqual((n.title, n.body, n.tags), ("New", "b", ["x"]))
    def test_r07_edit_tags_replace_all_tags(self): self.ok("add", "a", "--tag", "x", "--tag", "y"); self.ok("edit", "1", "--tag", "z"); self.assertEqual(self.store().get(1).tags, ["z"])
    def test_r07_edit_pin_and_unpin(self): self.ok("add", "a"); self.ok("edit", "1", "--pin"); self.assertTrue(self.store().get(1).pinned); self.ok("edit", "1", "--unpin"); self.assertFalse(self.store().get(1).pinned)
    def test_r07_edit_body_to_empty(self): self.ok("add", "a", "--body", "x"); self.ok("edit", "1", "--body", ""); self.assertEqual(self.store().get(1).body, "")
    def test_r07_edit_without_options(self): self.ok("add", "a"); rc, out, err = self.run_cli("edit", "1"); self.assertEqual(rc, 2); self.assertIn("Error: nothing to edit", err)
    def test_r07_delete(self): self.ok("add", "a"); self.assertEqual(self.ok("delete", "1"), "Deleted note 1\n"); self.assertEqual(self.ok("list"), "No notes.\n")
    def test_r07_search(self):
        self.ok("add", "Shopping", "--body", "milk"); self.ok("add", "Milk run", "--pin"); self.ok("add", "Other")
        self.assertEqual(self.ok("search", "milk"), "  2 * Milk run\n  1   Shopping\n"); self.assertEqual(self.ok("search", "nothing"), "No matches.\n")
    def test_r07_search_with_several_words_and_a_tag(self):
        self.ok("add", "a b", "--tag", "t"); self.ok("add", "a", "--tag", "t"); self.assertEqual(self.ok("search", "a", "b"), "  1   a b #t\n"); self.assertEqual(self.ok("search", "a", "--tag", "t"), "  1   a b #t\n  2   a #t\n")
    def test_r07_the_data_file_comes_from_notes_file(self): self.ok("add", "a"); self.assertTrue(os.path.exists(self.path))
    def test_r07_notes_now_is_the_clock(self): self.ok("add", "a"); self.assertEqual(self.store().get(1).created, T)
    def test_r07_default_file_is_notes_json_in_the_current_directory(self):
        env = {k: v for k, v in os.environ.items() if k != "NOTES_FILE"}; env["PYTHONPATH"] = WS
        p = subprocess.run([sys.executable, "-m", "notes", "add", "x"], cwd=self.dir, env=env, capture_output=True, text=True, timeout=30); self.assertEqual(p.returncode, 0, p.stderr); self.assertTrue(os.path.exists(os.path.join(self.dir, "notes.json")))
    def test_r07_python_dash_m_notes_works(self):
        env = dict(os.environ, PYTHONPATH=WS); p = subprocess.run([sys.executable, "-m", "notes", "add", "Via module", "--tag", "m"], cwd=self.dir, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual((p.returncode, p.stdout), (0, "Added note 1\n")); p = subprocess.run([sys.executable, "-m", "notes", "list"], cwd=self.dir, env=env, capture_output=True, text=True, timeout=30); self.assertEqual(p.stdout, "  1   Via module #m\n")
    def test_r07_main_returns_the_exit_status_instead_of_exiting(self): self.assertEqual(cli.main(["list"], out=io.StringIO()), 0)
    def test_r07_the_package_exports_the_public_names(self):
        import notes; [self.assertTrue(hasattr(notes, n), n) for n in ("Note", "ValidationError", "NoteStore", "NoteNotFound", "StorageError")]
    def test_r07_a_store_can_be_passed_to_main(self):
        s = self.store(os.path.join(self.dir, "inj.json")); out = io.StringIO(); self.assertEqual(cli.main(["add", "Injected"], store=s, out=out), 0); self.assertEqual(len(s), 1)
    def test_r07_list_order_pinned_then_id(self): self.ok("add", "a"); self.ok("add", "b", "--pin"); self.ok("add", "c", "--pin"); self.assertEqual([l[:3].strip() for l in self.ok("list").splitlines()], ["2", "3", "1"])

class TestCliErrors(Cli):
    def test_r08_export_and_import_commands(self):
        self.ok("add", "A", "--tag", "x"); self.ok("add", "B", "--pin"); self.assertEqual(self.ok("export", self.out), f"Exported 2 note(s) to {self.out}\n"); self.assertEqual(json.loads(self.read())["version"], 1)
        os.environ["NOTES_FILE"] = os.path.join(self.dir, "other.json"); self.assertEqual(self.ok("import", self.out), "Imported: added 2, skipped 0, overwritten 0\n"); self.assertEqual(self.ok("list"), "  2 * B\n  1   A #x\n")
    def test_r08_import_conflict_modes(self):
        self.ok("add", "A"); self.ok("export", self.out)
        self.assertEqual(self.ok("import", self.out), "Imported: added 0, skipped 1, overwritten 0\n"); self.assertEqual(self.ok("import", self.out, "--on-conflict", "overwrite"), "Imported: added 0, skipped 0, overwritten 1\n"); self.assertEqual(self.ok("import", self.out, "--on-conflict", "duplicate"), "Imported: added 1, skipped 0, overwritten 0\n")
    def test_r08_markdown_export(self): self.ok("add", "T", "--body", "B", "--tag", "x"); self.assertEqual(self.ok("export", self.out, "--format", "markdown"), f"Exported 1 note(s) to {self.out}\n"); self.assertEqual(self.read(), "# T\n\nTags: x\n\nB\n\n---\n")
    def test_r08_unknown_id_is_status_1(self):
        self.ok("add", "a")
        for argv in (("show", "9"), ("edit", "9", "--title", "x"), ("delete", "9")): rc, out, err = self.run_cli(*argv); self.assertEqual(rc, 1, argv); self.assertEqual(err, "Error: no note with id 9\n"); self.assertEqual(out, "")
    def test_r08_non_integer_id(self):
        for argv in (("show", "x"), ("edit", "x", "--title", "y"), ("delete", "x")): rc, out, err = self.run_cli(*argv); self.assertEqual(rc, 2); self.assertEqual(err, "Error: invalid note id 'x'\n")
    def test_r08_validation_errors_are_status_2(self):
        for argv, msg in ((("add", "  "), "title must not be empty"), (("add", "t", "--tag", "bad tag"), "invalid tag 'bad tag'"), ((("add", "x" * 81)), "title must be at most 80 characters")):
            rc, out, err = self.run_cli(*argv); self.assertEqual(rc, 2); self.assertEqual(err, f"Error: {msg}\n")
        self.assertFalse(os.path.exists(self.path))
    def test_r08_edit_validation_changes_nothing(self): self.ok("add", "a"); before = open(self.path).read(); rc, out, err = self.run_cli("edit", "1", "--title", ""); self.assertEqual(rc, 2); self.assertEqual(open(self.path).read(), before)
    def test_r08_corrupt_data_file(self):
        open(self.path, "w").write("{junk"); rc, out, err = self.run_cli("list"); self.assertEqual(rc, 2); self.assertEqual(err, f"Error: data file is corrupt: {self.path}\n"); self.assertEqual(open(self.path).read(), "{junk")
    def test_r08_bad_import_file_and_notes(self):
        p = os.path.join(self.dir, "bad.json"); open(p, "w").write("nope"); rc, out, err = self.run_cli("import", p); self.assertEqual((rc, err), (2, "Error: not a valid notes export file\n"))
        json.dump({"version": 1, "notes": [{"id": 1, "title": ""}]}, open(p, "w")); rc, out, err = self.run_cli("import", p); self.assertEqual((rc, err), (2, "Error: note 1: title must not be empty\n")); self.assertFalse(os.path.exists(self.path))
    def test_r08_missing_import_file(self): p = os.path.join(self.dir, "none.json"); rc, out, err = self.run_cli("import", p); self.assertEqual((rc, err), (2, f"Error: no such file: {p}\n"))
    def test_r08_invalid_format_and_conflict_mode(self):
        self.ok("add", "a"); rc, out, err = self.run_cli("export", self.out, "--format", "xml"); self.assertEqual((rc, err), (2, "Error: invalid format 'xml'\n")); self.assertFalse(os.path.exists(self.out))
        self.ok("export", self.out); rc, out, err = self.run_cli("import", self.out, "--on-conflict", "merge"); self.assertEqual((rc, err), (2, "Error: invalid conflict mode 'merge'\n"))
    def test_r08_no_command_prints_usage_with_status_2(self): rc, out, err = self.run_cli(); self.assertEqual(rc, 2); self.assertIn("usage", err.lower()); self.assertEqual(out, "")
    def test_r08_unknown_command_and_bad_options(self):
        for argv in (("frobnicate",), ("add",), ("add", "x", "--bogus"), ("edit", "1", "--pin", "--unpin")): rc, out, err = self.run_cli(*argv); self.assertEqual(rc, 2, argv); self.assertTrue(err)
    def test_r08_python_dash_m_exit_status_is_the_return_value(self):
        env = dict(os.environ, PYTHONPATH=WS); p = subprocess.run([sys.executable, "-m", "notes", "show", "5"], cwd=self.dir, env=env, capture_output=True, text=True, timeout=30); self.assertEqual((p.returncode, p.stderr), (1, "Error: no note with id 5\n"))
        p = subprocess.run([sys.executable, "-m", "notes"], cwd=self.dir, env=env, capture_output=True, text=True, timeout=30); self.assertEqual(p.returncode, 2)
    def test_r08_export_of_an_empty_store(self): self.assertEqual(self.ok("export", self.out), f"Exported 0 note(s) to {self.out}\n")
