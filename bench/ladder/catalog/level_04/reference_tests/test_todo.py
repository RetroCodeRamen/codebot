import json, os, subprocess, sys, tempfile, unittest

WS = os.getcwd()          # the runner starts every test file inside the copy of the workspace

class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="todo-test-"); self.file = os.path.join(self.dir, "data.json")
    def tearDown(self):
        import shutil; shutil.rmtree(self.dir, ignore_errors=True)
    def run_cli(self, *args, today="2024-06-15", env=None, cwd=None):
        e = dict(os.environ, TODO_FILE=self.file, TODO_TODAY=today, **(env or {}))
        p = subprocess.run([sys.executable, os.path.join(WS, "todo.py"), *args], capture_output=True, text=True, env=e, cwd=cwd or self.dir, timeout=30)
        self.assertNotIn("can't open file", p.stderr, "todo.py does not exist")            # a missing program must never look like a command that exited with status 2
        return p.returncode, p.stdout, p.stderr
    def ok(self, *args, **kw):
        rc, out, err = self.run_cli(*args, **kw); self.assertEqual(rc, 0, (args, out, err)); return out
    def data(self):
        with open(self.file) as f: return json.load(f)
    def add(self, *args): return self.ok("add", *args)
    def lines(self, *args): return self.ok("list", *args).splitlines()

class TestAdd(Cli):
    def test_r01_add_prints_the_new_id_and_title(self): self.assertEqual(self.add("Buy milk"), "Added task 1: Buy milk\n"); self.assertEqual(self.add("Pay rent"), "Added task 2: Pay rent\n")
    def test_r01_defaults(self): self.add("x"); t = self.data()["tasks"][0]; self.assertEqual((t["id"], t["title"], t["done"], t["priority"], t["due"], t["tags"]), (1, "x", False, "medium", None, []))
    def test_r01_priority_due_and_tags_are_stored(self):
        self.add("Pay rent", "--priority", "high", "--due", "2024-06-01", "--tag", "Money", "--tag", "home"); t = self.data()["tasks"][0]
        self.assertEqual((t["priority"], t["due"], t["tags"]), ("high", "2024-06-01", ["money", "home"]))
    def test_r01_a_repeated_tag_is_stored_once(self): self.add("x", "--tag", "a", "--tag", "A", "--tag", "b", "--tag", "a"); self.assertEqual(self.data()["tasks"][0]["tags"], ["a", "b"])
    def test_r01_the_title_is_trimmed(self): self.assertEqual(self.add("   padded   "), "Added task 1: padded\n"); self.assertEqual(self.data()["tasks"][0]["title"], "padded")
    def test_r01_an_empty_title_is_an_error(self):
        for t in ("", "   "):
            rc, out, err = self.run_cli("add", t); self.assertEqual(rc, 2); self.assertIn("Error: title must not be empty", err); self.assertEqual(out, "")
        self.assertFalse(os.path.exists(self.file))
    def test_r01_invalid_priority(self):
        rc, out, err = self.run_cli("add", "x", "--priority", "urgent"); self.assertEqual(rc, 2); self.assertIn("Error: invalid priority 'urgent'", err); self.assertFalse(os.path.exists(self.file))
    def test_r01_invalid_due_dates(self):
        for d in ("2024-02-30", "2024-13-01", "tomorrow", "2024-6-1", "24-06-01", "2024/06/01", ""):
            rc, out, err = self.run_cli("add", "x", "--due", d); self.assertEqual(rc, 2, d); self.assertIn(f"Error: invalid due date '{d}'", err)
        self.assertFalse(os.path.exists(self.file))
    def test_r01_a_leap_day_is_valid(self): self.add("x", "--due", "2024-02-29"); self.assertEqual(self.data()["tasks"][0]["due"], "2024-02-29")
    def test_r01_ids_increase(self):
        for i in range(1, 6): self.add(f"t{i}")
        self.assertEqual([t["id"] for t in self.data()["tasks"]], [1, 2, 3, 4, 5])
    def test_r01_titles_with_unicode_and_quotes(self): self.add('Café "quoted" ✓'); self.assertEqual(self.data()["tasks"][0]["title"], 'Café "quoted" ✓')

class TestList(Cli):
    def seed(self):
        self.add("Pay rent", "--priority", "high", "--due", "2024-06-01", "--tag", "money"); self.add("Buy milk", "--tag", "home", "--tag", "errand"); self.add("Old report", "--priority", "low"); self.ok("done", "2")
    def test_r02_line_format(self): self.seed(); self.assertEqual(self.lines("--all"), ["  1 [ ] high   Pay rent (due 2024-06-01) #money", "  2 [x] medium Buy milk #home #errand", "  3 [ ] low    Old report"])
    def test_r02_default_lists_only_pending(self): self.seed(); self.assertEqual([l[:6] for l in self.lines()], ["  1 [ ", "  3 [ "])
    def test_r02_done_lists_only_completed(self): self.seed(); self.assertEqual(self.lines("--done"), ["  2 [x] medium Buy milk #home #errand"])
    def test_r02_all_and_done_together_is_an_error(self): self.seed(); rc, out, err = self.run_cli("list", "--all", "--done"); self.assertEqual(rc, 2); self.assertIn("Error", err)
    def test_r02_empty_list(self): self.assertEqual(self.ok("list"), "No tasks.\n")
    def test_r02_no_match_prints_no_tasks(self): self.seed(); self.assertEqual(self.ok("list", "--tag", "nothing"), "No tasks.\n")
    def test_r02_tag_filter_is_case_insensitive(self): self.seed(); self.assertEqual(self.lines("--all", "--tag", "HOME"), ["  2 [x] medium Buy milk #home #errand"])
    def test_r02_priority_filter(self): self.seed(); self.assertEqual(self.lines("--all", "--priority", "high"), ["  1 [ ] high   Pay rent (due 2024-06-01) #money"])
    def test_r02_filters_combine_with_and(self): self.seed(); self.assertEqual(self.ok("list", "--all", "--tag", "money", "--priority", "low"), "No tasks.\n"); self.assertEqual(len(self.lines("--all", "--tag", "money", "--priority", "high")), 1)
    def test_r02_id_alignment_above_nine(self):
        for i in range(1, 12): self.add(f"t{i}")
        ls = self.lines(); self.assertTrue(ls[0].startswith("  1 [ ] medium t1")); self.assertTrue(ls[-1].startswith(" 11 [ ] medium t11"))
    def test_r02_priority_column_is_six_wide(self): self.add("a", "--priority", "low"); self.add("b", "--priority", "high"); self.assertEqual(self.lines(), ["  1 [ ] low    a", "  2 [ ] high   b"])
    def test_r02_list_never_creates_the_data_file(self): self.ok("list"); self.assertFalse(os.path.exists(self.file))
    def test_r02_list_with_an_invalid_priority_filter(self): rc, out, err = self.run_cli("list", "--priority", "urgent"); self.assertEqual(rc, 2)
    def test_r02_tags_keep_the_order_they_were_added(self): self.add("x", "--tag", "zeta", "--tag", "alpha"); self.assertEqual(self.lines(), ["  1 [ ] medium x #zeta #alpha"])

class TestSort(Cli):
    def seed(self):
        self.add("a", "--priority", "low", "--due", "2024-07-01"); self.add("b", "--priority", "high"); self.add("c", "--priority", "medium", "--due", "2024-05-01"); self.add("d", "--priority", "high", "--due", "2024-06-01")
    def ids(self, *args): return [int(l[:3]) for l in self.lines(*args)]
    def test_r03_default_is_id_order(self): self.seed(); self.assertEqual(self.ids(), [1, 2, 3, 4]); self.assertEqual(self.ids("--sort", "id"), [1, 2, 3, 4])
    def test_r03_priority_order_with_ties_by_id(self): self.seed(); self.assertEqual(self.ids("--sort", "priority"), [2, 4, 3, 1])
    def test_r03_due_order_puts_undated_tasks_last(self): self.seed(); self.assertEqual(self.ids("--sort", "due"), [3, 4, 1, 2])
    def test_r03_undated_tasks_are_ordered_by_id(self): self.add("x"); self.add("y"); self.add("z", "--due", "2024-01-01"); self.assertEqual(self.ids("--sort", "due"), [3, 1, 2])
    def test_r03_sort_applies_after_filters(self): self.seed(); self.assertEqual(self.ids("--sort", "priority", "--priority", "high"), [2, 4])
    def test_r03_invalid_sort_key(self): self.seed(); rc, out, err = self.run_cli("list", "--sort", "name"); self.assertEqual(rc, 2); self.assertIn("Error: invalid sort key 'name'", err)
    def test_r03_sort_with_all(self): self.seed(); self.ok("done", "3"); self.assertEqual(self.ids("--all", "--sort", "due"), [3, 4, 1, 2])

class TestDoneReopen(Cli):
    def test_r04_done_prints_and_persists(self): self.add("a"); self.assertEqual(self.ok("done", "1"), "Completed task 1\n"); self.assertTrue(self.data()["tasks"][0]["done"])
    def test_r04_done_several_in_the_order_given(self):
        for t in "abc": self.add(t)
        self.assertEqual(self.ok("done", "3", "1"), "Completed task 3\nCompleted task 1\n")
    def test_r04_an_already_done_task_is_not_an_error(self): self.add("a"); self.ok("done", "1"); self.assertEqual(self.ok("done", "1"), "Task 1 is already done\n")
    def test_r04_reopen(self): self.add("a"); self.ok("done", "1"); self.assertEqual(self.ok("reopen", "1"), "Reopened task 1\n"); self.assertFalse(self.data()["tasks"][0]["done"])
    def test_r04_unknown_id_is_status_1_and_changes_nothing(self):
        self.add("a"); self.add("b"); before = open(self.file).read(); rc, out, err = self.run_cli("done", "1", "9"); self.assertEqual(rc, 1); self.assertIn("Error: no task with id 9", err); self.assertEqual(open(self.file).read(), before)
        self.assertFalse(self.data()["tasks"][0]["done"])
    def test_r04_all_unknown_ids_are_reported(self): self.add("a"); rc, out, err = self.run_cli("done", "7", "8"); self.assertEqual(rc, 1); self.assertIn("no task with id 7", err); self.assertIn("no task with id 8", err)
    def test_r04_non_integer_id_is_status_2(self): self.add("a"); rc, out, err = self.run_cli("done", "abc"); self.assertEqual(rc, 2); self.assertIn("Error: invalid task id 'abc'", err)
    def test_r04_reopen_unknown_id(self): self.add("a"); rc, out, err = self.run_cli("reopen", "5"); self.assertEqual(rc, 1)
    def test_r04_done_needs_an_id(self): rc, out, err = self.run_cli("done"); self.assertEqual(rc, 2)
    def test_r04_done_does_not_change_other_fields(self):
        self.add("a", "--priority", "high", "--tag", "t"); before = self.data()["tasks"][0]; self.ok("done", "1"); after = self.data()["tasks"][0]; before["done"] = True; self.assertEqual(before, after)

class TestDelete(Cli):
    def test_r05_delete_prints_and_removes(self): self.add("a"); self.add("b"); self.assertEqual(self.ok("delete", "1"), "Deleted task 1\n"); self.assertEqual([t["id"] for t in self.data()["tasks"]], [2])
    def test_r05_ids_are_never_reused(self):
        self.add("a"); self.add("b"); self.ok("delete", "2"); self.assertEqual(self.add("c"), "Added task 3: c\n"); self.ok("delete", "3", "1"); self.assertEqual(self.add("d"), "Added task 4: d\n")
    def test_r05_delete_everything_then_add_continues_numbering(self): self.add("a"); self.ok("delete", "1"); self.assertEqual(self.data()["tasks"], []); self.assertEqual(self.add("b"), "Added task 2: b\n")
    def test_r05_unknown_id_is_all_or_nothing(self):
        self.add("a"); self.add("b"); rc, out, err = self.run_cli("delete", "1", "5"); self.assertEqual(rc, 1); self.assertIn("Error: no task with id 5", err); self.assertEqual(len(self.data()["tasks"]), 2)
    def test_r05_delete_the_same_id_twice_in_one_command(self): self.add("a"); self.add("b"); rc, out, err = self.run_cli("delete", "1", "1"); self.assertEqual(rc, 0); self.assertEqual([t["id"] for t in self.data()["tasks"]], [2])
    def test_r05_clear_done(self):
        for t in "abc": self.add(t)
        self.ok("done", "1", "3"); self.assertEqual(self.ok("clear-done"), "Removed 2 completed task(s)\n"); self.assertEqual([t["id"] for t in self.data()["tasks"]], [2]); self.assertEqual(self.add("d"), "Added task 4: d\n")
    def test_r05_clear_done_with_nothing_to_remove(self): self.add("a"); self.assertEqual(self.ok("clear-done"), "Removed 0 completed task(s)\n"); self.assertEqual(len(self.data()["tasks"]), 1)
    def test_r05_non_integer_id(self): rc, out, err = self.run_cli("delete", "x"); self.assertEqual(rc, 2)

class TestEdit(Cli):
    def setUp(self): super().setUp(); self.add("Orig", "--priority", "low", "--due", "2024-07-01", "--tag", "a", "--tag", "b")
    def task(self): return self.data()["tasks"][0]
    def test_r06_title(self): self.assertEqual(self.ok("edit", "1", "--title", "  New  "), "Updated task 1\n"); self.assertEqual(self.task()["title"], "New")
    def test_r06_priority(self): self.ok("edit", "1", "--priority", "high"); self.assertEqual(self.task()["priority"], "high")
    def test_r06_due_set_and_remove(self): self.ok("edit", "1", "--due", "2025-01-02"); self.assertEqual(self.task()["due"], "2025-01-02"); self.ok("edit", "1", "--due", "none"); self.assertIsNone(self.task()["due"])
    def test_r06_add_and_remove_tags(self): self.ok("edit", "1", "--add-tag", "C", "--add-tag", "a", "--remove-tag", "b"); self.assertEqual(self.task()["tags"], ["a", "c"])
    def test_r06_removing_an_absent_tag_is_ignored(self): self.ok("edit", "1", "--remove-tag", "zzz"); self.assertEqual(self.task()["tags"], ["a", "b"])
    def test_r06_only_the_given_fields_change(self): self.ok("edit", "1", "--title", "T"); t = self.task(); self.assertEqual((t["priority"], t["due"], t["tags"], t["done"]), ("low", "2024-07-01", ["a", "b"], False))
    def test_r06_nothing_to_edit(self): rc, out, err = self.run_cli("edit", "1"); self.assertEqual(rc, 2); self.assertIn("Error: nothing to edit", err)
    def test_r06_unknown_id_is_status_1(self): rc, out, err = self.run_cli("edit", "9", "--title", "x"); self.assertEqual(rc, 1); self.assertIn("no task with id 9", err)
    def test_r06_invalid_values_change_nothing(self):
        before = open(self.file).read()
        for args in (["--priority", "x"], ["--due", "2024-02-31"], ["--title", "  "], ["--priority", "high", "--due", "bad"]):
            rc, out, err = self.run_cli("edit", "1", *args); self.assertEqual(rc, 2, args); self.assertEqual(open(self.file).read(), before)
    def test_r06_several_changes_at_once(self): self.ok("edit", "1", "--title", "Z", "--priority", "medium", "--due", "none", "--add-tag", "q"); t = self.task(); self.assertEqual((t["title"], t["priority"], t["due"], t["tags"]), ("Z", "medium", None, ["a", "b", "q"]))
    def test_r06_edit_keeps_the_done_state(self): self.ok("done", "1"); self.ok("edit", "1", "--title", "Z"); self.assertTrue(self.task()["done"])
    def test_r06_a_tag_added_and_removed_in_one_edit_is_removed(self): self.ok("edit", "1", "--add-tag", "new", "--remove-tag", "new"); self.assertEqual(self.task()["tags"], ["a", "b"])

class TestStats(Cli):
    def test_r07_empty(self): self.assertEqual(self.ok("stats"), "Total: 0\nDone: 0\nPending: 0\nOverdue: 0\n")
    def test_r07_counts_and_overdue(self):
        self.add("a", "--due", "2024-06-01"); self.add("b", "--due", "2024-06-15"); self.add("c", "--due", "2024-07-01"); self.add("d"); self.add("e", "--due", "2024-01-01"); self.ok("done", "5")
        self.assertEqual(self.ok("stats"), "Total: 5\nDone: 1\nPending: 4\nOverdue: 1\n")
    def test_r07_overdue_is_strictly_before_today(self): self.add("a", "--due", "2024-06-15"); self.assertEqual(self.ok("stats").splitlines()[3], "Overdue: 0"); self.assertEqual(self.ok("stats", today="2024-06-16").splitlines()[3], "Overdue: 1")
    def test_r07_done_tasks_are_never_overdue(self): self.add("a", "--due", "2020-01-01"); self.ok("done", "1"); self.assertEqual(self.ok("stats").splitlines()[3], "Overdue: 0")
    def test_r07_stats_after_delete(self): self.add("a"); self.add("b"); self.ok("delete", "1"); self.assertEqual(self.ok("stats").splitlines()[0], "Total: 1")
    def test_r07_exactly_four_lines(self): self.assertEqual(len(self.ok("stats").splitlines()), 4)
    def test_r07_stats_never_creates_the_file(self): self.ok("stats"); self.assertFalse(os.path.exists(self.file))

class TestPersistence(Cli):
    def test_r08_data_survives_between_processes(self): self.add("a"); self.add("b"); self.ok("done", "1"); self.assertEqual(len(self.lines("--all")), 2)
    def test_r08_file_structure(self):
        self.add("a", "--tag", "x"); d = self.data(); self.assertEqual(set(d), {"next_id", "tasks"}); self.assertEqual(d["next_id"], 2); self.assertEqual(set(d["tasks"][0]), {"id", "title", "done", "priority", "due", "tags"})
    def test_r08_next_id_survives_deleting_everything(self): self.add("a"); self.ok("delete", "1"); self.assertEqual(self.data()["next_id"], 2)
    def test_r08_default_file_name_is_tasks_json_in_the_current_directory(self):
        e = {k: v for k, v in os.environ.items() if k != "TODO_FILE"}; p = subprocess.run([sys.executable, os.path.join(WS, "todo.py"), "add", "x"], capture_output=True, text=True, cwd=self.dir, env=e, timeout=30)
        self.assertEqual(p.returncode, 0); self.assertTrue(os.path.exists(os.path.join(self.dir, "tasks.json")))
    def test_r08_a_missing_file_means_no_tasks_and_is_created_by_the_first_change(self): self.assertFalse(os.path.exists(self.file)); self.add("a"); self.assertTrue(os.path.exists(self.file))
    def test_r08_a_corrupt_file_fails_every_command_and_is_left_alone(self):
        for junk in ("{not json", "[]", '{"next_id": 1}', '{"next_id": "x", "tasks": []}', '{"next_id": 1, "tasks": [{"id": 1}]}', ""):
            with open(self.file, "w") as f: f.write(junk)
            for cmd in (["list"], ["add", "x"], ["done", "1"], ["stats"], ["clear-done"]):
                rc, out, err = self.run_cli(*cmd); self.assertEqual(rc, 2, (junk, cmd)); self.assertIn("Error: data file is corrupt", err)
            self.assertEqual(open(self.file).read(), junk)
    def test_r08_no_temporary_files_are_left_behind(self):
        self.add("a"); self.add("b"); self.ok("done", "1"); self.ok("delete", "2"); self.assertEqual(sorted(os.listdir(self.dir)), ["data.json"])
    def test_r08_the_file_is_always_complete_json_after_every_command(self):
        for cmd in (["add", "a"], ["add", "b"], ["done", "1"], ["edit", "2", "--title", "z"], ["delete", "1"], ["clear-done"]): self.ok(*cmd); self.data()
    def test_r08_no_command_prints_usage_to_stderr_with_status_2(self): rc, out, err = self.run_cli(); self.assertEqual(rc, 2); self.assertIn("usage", err.lower()); self.assertEqual(out, "")
    def test_r08_unknown_command(self): rc, out, err = self.run_cli("frobnicate"); self.assertEqual(rc, 2); self.assertIn("usage", err.lower())
    def test_r08_help_goes_to_stdout_with_status_0(self): rc, out, err = self.run_cli("--help"); self.assertEqual(rc, 0); self.assertIn("usage", out.lower())
    def test_r08_unknown_option(self): rc, out, err = self.run_cli("add", "x", "--bogus"); self.assertEqual(rc, 2); self.assertFalse(os.path.exists(self.file))
    def test_r08_errors_go_to_stderr_not_stdout(self): self.add("a"); rc, out, err = self.run_cli("done", "9"); self.assertEqual(out, ""); self.assertTrue(err.startswith("Error:"))
    def test_r08_the_data_file_may_be_in_another_directory(self):
        sub = os.path.join(self.dir, "sub"); os.makedirs(sub); self.file = os.path.join(sub, "x.json"); self.add("a"); self.assertTrue(os.path.exists(self.file))
