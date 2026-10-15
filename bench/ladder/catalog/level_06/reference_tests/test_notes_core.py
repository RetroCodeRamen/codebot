import json, os, tempfile, unittest
from notes.models import Note
from notes.validation import ValidationError, clean_title, clean_body, clean_tags
from notes.storage import NoteStore, NoteNotFound, StorageError
from notes.search import search

class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="notes-test-"); self.path = os.path.join(self.dir, "n.json"); self.t = "2024-01-01T10:00:00"
    def tearDown(self):
        import shutil; shutil.rmtree(self.dir, ignore_errors=True)
    def store(self, path=None): return NoteStore(path or self.path, clock=lambda: self.t)

class TestModel(unittest.TestCase):
    def test_r01_defaults_and_field_order(self):
        n = Note(1, "T"); self.assertEqual((n.body, n.tags, n.created, n.updated, n.pinned), ("", [], "", "", False))
        self.assertEqual(list(n.to_dict()), ["id", "title", "body", "tags", "created", "updated", "pinned"])
    def test_r01_to_dict_is_a_plain_dict(self): d = Note(2, "T", "b", ["x"], "c", "u", True).to_dict(); self.assertIs(type(d), dict); self.assertEqual(d, {"id": 2, "title": "T", "body": "b", "tags": ["x"], "created": "c", "updated": "u", "pinned": True})
    def test_r01_round_trip(self): n = Note(3, "T", "b", ["x", "y"], "c", "u", True); self.assertEqual(Note.from_dict(n.to_dict()), n)
    def test_r01_from_dict_defaults(self): n = Note.from_dict({"id": 4, "title": "T"}); self.assertEqual((n.body, n.tags, n.created, n.updated, n.pinned), ("", [], "", "", False))
    def test_r01_from_dict_copies_the_tag_list(self): d = {"id": 1, "title": "T", "tags": ["a"]}; n = Note.from_dict(d); n.tags.append("b"); self.assertEqual(d["tags"], ["a"])
    def test_r01_default_tag_lists_are_not_shared(self): a, b = Note(1, "a"), Note(2, "b"); a.tags.append("x"); self.assertEqual(b.tags, [])
    def test_r01_equality(self): self.assertEqual(Note(1, "T"), Note(1, "T")); self.assertNotEqual(Note(1, "T"), Note(2, "T"))

class TestValidation(unittest.TestCase):
    def msg(self, fn, *a):
        with self.assertRaises(ValidationError) as cm: fn(*a)
        return str(cm.exception)
    def test_r02_error_is_a_value_error(self): self.assertTrue(issubclass(ValidationError, ValueError))
    def test_r02_title_is_stripped(self): self.assertEqual(clean_title("  Hello  "), "Hello")
    def test_r02_title_errors(self):
        self.assertEqual(self.msg(clean_title, 5), "title must be a string"); self.assertEqual(self.msg(clean_title, None), "title must be a string")
        self.assertEqual(self.msg(clean_title, "   "), "title must not be empty"); self.assertEqual(self.msg(clean_title, ""), "title must not be empty")
        self.assertEqual(self.msg(clean_title, "a\nb"), "title must be a single line"); self.assertEqual(self.msg(clean_title, "a\rb"), "title must be a single line")
        self.assertEqual(self.msg(clean_title, "x" * 81), "title must be at most 80 characters")
    def test_r02_title_length_boundary(self): self.assertEqual(len(clean_title("x" * 80)), 80)
    def test_r02_a_trailing_newline_is_stripped_before_the_single_line_check(self): self.assertEqual(clean_title("title\n"), "title")
    def test_r02_stripping_happens_before_the_length_check(self): self.assertEqual(clean_title(" " + "x" * 80 + " "), "x" * 80)
    def test_r02_body(self):
        self.assertEqual(clean_body("a\nb"), "a\nb"); self.assertEqual(clean_body(""), ""); self.assertEqual(len(clean_body("x" * 10000)), 10000)
        self.assertEqual(self.msg(clean_body, 5), "body must be a string"); self.assertEqual(self.msg(clean_body, "x" * 10001), "body must be at most 10000 characters")
    def test_r02_body_is_not_stripped(self): self.assertEqual(clean_body("  keep  \n"), "  keep  \n")
    def test_r02_tags_are_lowercased_stripped_and_deduplicated(self): self.assertEqual(clean_tags([" Work ", "work", "HOME", "a_b-c"]), ["work", "home", "a_b-c"])
    def test_r02_tags_accept_any_iterable_of_strings(self): self.assertEqual(clean_tags(("a", "b")), ["a", "b"]); self.assertEqual(clean_tags(x for x in "ab"), ["a", "b"]); self.assertEqual(clean_tags([]), [])
    def test_r02_tag_container_errors(self):
        for bad in ("abc", 5, None): self.assertEqual(self.msg(clean_tags, bad), "tags must be a list of strings")
        self.assertEqual(self.msg(clean_tags, ["a", 5]), "tags must be a list of strings")
    def test_r02_invalid_tags(self):
        for bad, shown in (("", ""), ("-x", "-x"), ("_x", "_x"), ("a b", "a b"), ("x" * 21, "x" * 21), ("ä", "ä"), ("a.b", "a.b"), ("  Bad Tag ", "bad tag")): self.assertEqual(self.msg(clean_tags, [bad]), f"invalid tag '{shown}'")
    def test_r02_tag_length_boundary(self): self.assertEqual(clean_tags(["x" * 20]), ["x" * 20]); self.assertEqual(clean_tags(["0"]), ["0"])
    def test_r02_tag_count_limit(self):
        self.assertEqual(len(clean_tags([f"t{i}" for i in range(10)])), 10); self.assertEqual(self.msg(clean_tags, [f"t{i}" for i in range(11)]), "at most 10 tags are allowed")
    def test_r02_duplicates_do_not_count_towards_the_limit(self): self.assertEqual(len(clean_tags([f"t{i % 10}" for i in range(30)])), 10)
    def test_r02_tags_result_is_a_list(self): self.assertIs(type(clean_tags(("a",))), list)

class TestStore(Base):
    def test_r03_add_assigns_ids_and_timestamps(self):
        s = self.store(); n = s.add("First", "body", ["Work"], True); self.assertEqual((n.id, n.title, n.body, n.tags, n.pinned, n.created, n.updated), (1, "First", "body", ["work"], True, self.t, self.t)); self.assertEqual(s.add("Second").id, 2)
    def test_r03_get_and_not_found(self):
        s = self.store(); s.add("A"); self.assertEqual(s.get(1).title, "A")
        with self.assertRaises(NoteNotFound) as cm: s.get(9)
        self.assertEqual(cm.exception.args[0], 9); self.assertTrue(issubclass(NoteNotFound, KeyError))
    def test_r03_ids_are_never_reused(self):
        s = self.store(); s.add("a"); s.add("b"); s.delete(2); self.assertEqual(s.add("c").id, 3); s.delete(1); s.delete(3); self.assertEqual(s.add("d").id, 4)
    def test_r03_persistence_between_instances(self):
        s = self.store(); s.add("a", tags=["x"]); s.add("b"); s.delete(1); s2 = self.store(); self.assertEqual([n.title for n in s2.all()], ["b"]); self.assertEqual(s2.add("c").id, 3)
    def test_r03_file_format(self):
        s = self.store(); s.add("a", "b", ["t"], True); d = json.load(open(self.path)); self.assertEqual(set(d), {"next_id", "notes"}); self.assertEqual(d["next_id"], 2); self.assertEqual(d["notes"][0], Note(1, "a", "b", ["t"], self.t, self.t, True).to_dict())
    def test_r03_the_file_is_created_by_the_first_change_only(self):
        s = self.store(); self.assertFalse(os.path.exists(self.path)); s.all(); len(s); self.assertFalse(os.path.exists(self.path)); s.add("a"); self.assertTrue(os.path.exists(self.path))
    def test_r03_add_validates(self):
        s = self.store()
        for kw in ({"title": ""}, {"title": "a", "tags": ["bad tag"]}, {"title": "a", "body": 5}):
            with self.assertRaises(ValidationError): s.add(**kw)
        self.assertEqual(len(s), 0); self.assertFalse(os.path.exists(self.path))
    def test_r03_update_changes_only_the_given_fields_and_the_updated_stamp(self):
        s = self.store(); s.add("a", "b", ["t"]); self.t = "2024-02-02T12:00:00"; n = s.update(1, title="New"); self.assertEqual((n.title, n.body, n.tags, n.pinned, n.created, n.updated), ("New", "b", ["t"], False, "2024-01-01T10:00:00", self.t))
    def test_r03_update_all_fields(self): s = self.store(); s.add("a"); n = s.update(1, title=" T ", body="B", tags=["X", "y"], pinned=True); self.assertEqual((n.title, n.body, n.tags, n.pinned), ("T", "B", ["x", "y"], True))
    def test_r03_a_failed_update_changes_nothing(self):
        s = self.store(); s.add("a", "b"); before = open(self.path).read()
        with self.assertRaises(ValidationError): s.update(1, title="ok", body=5)
        self.assertEqual(open(self.path).read(), before); self.assertEqual(s.get(1).title, "a")
    def test_r03_update_unknown_field_and_unknown_id(self):
        s = self.store(); s.add("a")
        with self.assertRaises(TypeError): s.update(1, color="red")
        with self.assertRaises(NoteNotFound): s.update(5, title="x")
    def test_r03_delete_returns_the_note(self): s = self.store(); s.add("a"); n = s.delete(1); self.assertEqual(n.title, "a"); self.assertEqual(len(s), 0)
    def test_r03_delete_unknown(self):
        with self.assertRaises(NoteNotFound): self.store().delete(1)
    def test_r03_all_is_sorted_by_id(self): s = self.store(); [s.add(t) for t in "cba"]; self.assertEqual([n.id for n in s.all()], [1, 2, 3])
    def test_r03_len(self): s = self.store(); self.assertEqual(len(s), 0); s.add("a"); s.add("b"); self.assertEqual(len(s), 2)
    def test_r03_corrupt_files_raise_storage_error_and_are_left_alone(self):
        for junk in ("{nope", "[]", "", '{"next_id": 1}', '{"next_id": 1, "notes": [{"id": 1}]}', '{"next_id": "x", "notes": []}'):
            open(self.path, "w").write(junk)
            with self.assertRaises(StorageError): self.store()
            self.assertEqual(open(self.path).read(), junk)
    def test_r03_restore_keeps_id_and_timestamps_and_advances_next_id(self):
        s = self.store(); s.restore(Note(7, "R", "b", ["x"], "c1", "u1", True)); n = s.get(7); self.assertEqual((n.created, n.updated, n.pinned), ("c1", "u1", True)); self.assertEqual(s.add("next").id, 8)
    def test_r03_restore_rejects_a_used_id(self):
        s = self.store(); s.add("a")
        with self.assertRaises(ValueError): s.restore(Note(1, "dup"))
    def test_r03_restore_below_next_id_keeps_next_id(self): s = self.store(); s.add("a"); s.add("b"); s.delete(1); s.restore(Note(1, "back")); self.assertEqual(s.add("c").id, 3)
    def test_r03_restore_is_saved(self): s = self.store(); s.restore(Note(5, "R")); self.assertEqual(self.store().get(5).title, "R")
    def test_r03_no_temporary_files_are_left(self): s = self.store(); s.add("a"); s.update(1, title="b"); s.delete(1); self.assertEqual(os.listdir(self.dir), ["n.json"])
    def test_r03_default_clock_gives_an_iso_timestamp(self):
        s = NoteStore(self.path); n = s.add("a"); import re; self.assertRegex(n.created, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"); self.assertEqual(n.created, n.updated)
    def test_r03_notes_returned_by_get_are_the_stored_ones(self): s = self.store(); s.add("a"); self.assertIs(s.get(1), s.get(1))

class TestSearch(unittest.TestCase):
    def notes(self):
        return [Note(1, "Shopping list", "milk eggs bread", ["home"]), Note(2, "Python tips", "use list comprehensions", ["code", "python"]), Note(3, "Milk run", "buy milk", ["home", "errand"], pinned=True), Note(4, "Meeting", "discuss python roadmap", ["work"])]
    def ids(self, *a, **k): return [n.id for n in search(self.notes(), *a, **k)]
    def test_r04_every_term_must_match(self): self.assertEqual(self.ids("milk bread"), [1]); self.assertEqual(self.ids("milk"), [3, 1])
    def test_r04_case_insensitive(self): self.assertEqual(self.ids("PYTHON"), [2, 4]); self.assertEqual(self.ids("sHoPpInG"), [1])
    def test_r04_terms_match_title_body_or_tags(self): self.assertEqual(self.ids("errand"), [3]); self.assertEqual(self.ids("roadmap"), [4]); self.assertEqual(self.ids("comprehensions"), [2])
    def test_r04_scores_rank_title_over_tags_over_body(self):
        notes = [Note(1, "none", "alpha"), Note(2, "none", "x", ["alpha"]), Note(3, "alpha", "x")]; self.assertEqual([n.id for n in search(notes, "alpha")], [3, 2, 1])
    def test_r04_scores_add_up_over_terms_and_places(self):
        notes = [Note(1, "a b", ""), Note(2, "a", "b"), Note(3, "x", "a b", ["a"])]; self.assertEqual([n.id for n in search(notes, "a b")], [1, 2, 3])        # 6, 4, 4+... see spec: note 3 = (1+2)+(1) = 4; ties by id
    def test_r04_a_term_counts_once_per_place_even_if_repeated(self): notes = [Note(1, "a a a", ""), Note(2, "b", "a", ["a"])]; self.assertEqual([n.id for n in search(notes, "a")], [1, 2])
    def test_r04_pinned_wins_ties(self): notes = [Note(1, "same", ""), Note(2, "same", "", pinned=True)]; self.assertEqual([n.id for n in search(notes, "same")], [2, 1])
    def test_r04_score_beats_pinned(self): notes = [Note(1, "x", "q", pinned=True), Note(2, "q", "")]; self.assertEqual([n.id for n in search(notes, "q")], [2, 1])
    def test_r04_ties_by_id(self): notes = [Note(3, "t", ""), Note(1, "t", ""), Note(2, "t", "")]; self.assertEqual([n.id for n in search(notes, "t")], [1, 2, 3])
    def test_r04_empty_query_matches_everything_pinned_first(self): self.assertEqual(self.ids(""), [3, 1, 2, 4]); self.assertEqual(self.ids("   "), [3, 1, 2, 4]); self.assertEqual(self.ids(), [3, 1, 2, 4])
    def test_r04_tag_filter(self): self.assertEqual(self.ids("", tags=["home"]), [3, 1]); self.assertEqual(self.ids("", tags=["HOME", "errand"]), [3]); self.assertEqual(self.ids("", tags=["nothing"]), [])
    def test_r04_tag_filter_combines_with_the_query(self): self.assertEqual(self.ids("milk", tags=["errand"]), [3]); self.assertEqual(self.ids("python", tags=["work"]), [4])
    def test_r04_no_match(self): self.assertEqual(self.ids("zzz"), [])
    def test_r04_the_input_list_is_not_modified(self): notes = self.notes(); before = [n.id for n in notes]; search(notes, "milk"); self.assertEqual([n.id for n in notes], before)
    def test_r04_returns_the_note_objects(self): notes = self.notes(); self.assertIs(search(notes, "meeting")[0], notes[3])
    def test_r04_only_whole_query_terms_split_on_whitespace(self): self.assertEqual(self.ids("  milk \t eggs "), [1])
    def test_r04_a_term_found_only_through_the_joined_tags_text(self): notes = [Note(1, "x", "", ["alpha", "beta"])]; self.assertEqual([n.id for n in search(notes, "beta")], [1])
