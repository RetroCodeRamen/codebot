"""F7 + F8: constrained actions, validation before apply, deterministic context. Uses a scripted fake Ollama."""
import json, os, unittest
from helpers import Sandboxed
from fake_ollama import FakeOllama, A
from codebot import engine, worker, packer, planops, sizing, tasks as T

PLAN = """
# Goal
Tiny adder.
# Definition of done
a.py has add.
# Tasks
- [ ] 1. write add in a.py
  - check: `python3 -c "import a; assert a.add(1,2)==3, 'add is wrong'"`
  - files: a.py
  - size: S
"""
ADD = "def add(a, b):\n    return a + b\n"

class Base(Sandboxed):
    def setUp(self):
        super().setUp(); self.fake = None; self.write_plan(PLAN); engine.freeze_project(self.proj())
    def tearDown(self):
        if self.fake: self.fake.stop()
        super().tearDown()
    def model(self, script, **kw):
        self.fake = FakeOllama(script, **kw); self.set_env(WORKER="native", OLLAMA_PORT=self.fake.port, **{k: str(v) for k, v in kw.get("env", {}).items()}) if False else self.set_env(WORKER="native", OLLAMA_PORT=self.fake.port)
        return self.fake
    def attempt(self, script, **kw):
        self.model(script, **kw.pop("fake", {}))
        p = self.proj(); task = T.TaskStore(p).load()["tasks"][0]
        return worker.run_attempt(p, task, 1, kw.pop("last_error", ""), **kw)
    def file(self, rel):
        p = os.path.join(self.code, rel)
        return open(p).read() if os.path.exists(p) else None
    def results(self):   # the RESULT messages the model was shown, in order
        last = self.fake.requests[-1]["messages"]
        return [m["content"] for m in last if m["role"] == "user" and m["content"].startswith("RESULT")]

class TestEndToEnd(Base):
    def test_engine_step_with_native_worker(self):
        self.model([A("read_file", path="README.md"), A("write_file", path="a.py", content=ADD), A("run", command="python3 -c 'import a; print(a.add(1,2))'"), A("finish", summary="done")])
        e = engine.step(self.proj()); self.assertEqual(e["event"], "task_done", e)
        self.assertEqual(self.file("a.py"), ADD)
        r0 = self.fake.requests[0]
        self.assertFalse(r0["think"]); self.assertIn("anyOf", r0["format"]); self.assertEqual(r0["messages"][0]["role"], "system")
        u = r0["messages"][1]["content"]
        for s in ("write add in a.py", "ACCEPTANCE CHECK", "add is wrong", "REPO MAP"): self.assertIn(s, u)

    def test_claiming_success_is_not_success(self):
        self.model([A("finish", summary="I did everything, all tests pass")])
        e = engine.step(self.proj()); self.assertEqual(e["event"], "task_failed")

class TestValidation(Base):
    def test_malformed_json_is_repaired_once_then_works(self):
        self.attempt(["not json at all", A("write_file", path="a.py", content=ADD), A("finish", summary="ok")])
        self.assertEqual(self.file("a.py"), ADD); self.assertTrue(any("rejected" in r for r in self.results()))

    def test_three_malformed_replies_abort(self):
        out = self.attempt(["x", "{", "[]"]); self.assertEqual(out["ended"], "too many malformed replies"); self.assertIsNone(self.file("a.py"))

    def test_missing_field_is_rejected(self):
        self.attempt([{"why": "x", "action": "write_file", "path": "a.py"}, A("finish", summary="s")])
        self.assertIsNone(self.file("a.py")); self.assertTrue(any("missing: content" in r for r in self.results()))

    def test_unknown_action_is_rejected(self):
        self.attempt([A("format_disk"), A("finish", summary="s")]); self.assertTrue(any("unknown action" in r for r in self.results()))

    def test_python_syntax_error_is_never_written(self):
        self.attempt([A("write_file", path="a.py", content="def add(a, b)\n    return a+b\n"), A("finish", summary="s")])
        self.assertIsNone(self.file("a.py")); self.assertTrue(any("NOT written" in r and "syntax" in r for r in self.results()))

    def test_bad_json_and_shell_files_are_refused(self):
        self.attempt([A("write_file", path="c.json", content="{oops"), A("write_file", path="x.sh", content="if then\n"), A("finish", summary="s")])
        self.assertIsNone(self.file("c.json")); self.assertIsNone(self.file("x.sh"))

    def test_edit_requires_exactly_one_match(self):
        self.write_code("a.py", "x = 1\ny = 1\n")
        self.attempt([A("edit_file", path="a.py", search="= 1", replace="= 2"), A("edit_file", path="a.py", search="zzz", replace="q"), A("finish", summary="s")])
        self.assertEqual(self.file("a.py"), "x = 1\ny = 1\n")
        rs = " ".join(self.results()); self.assertIn("appears 2 times", rs); self.assertIn("not found", rs)

    def test_edit_that_breaks_syntax_is_refused(self):
        self.write_code("a.py", ADD)
        self.attempt([A("edit_file", path="a.py", search="return a + b", replace="return a +"), A("finish", summary="s")])
        self.assertEqual(self.file("a.py"), ADD); self.assertTrue(any("would be broken" in r for r in self.results()))

    def test_valid_edit_applies(self):
        self.write_code("a.py", "def add(a, b):\n    return 0\n")
        self.attempt([A("edit_file", path="a.py", search="return 0", replace="return a + b"), A("finish", summary="s")])
        self.assertIn("return a + b", self.file("a.py"))

    def test_paths_cannot_escape(self):
        outside = os.path.join(self.tmp, "outside.txt"); os.symlink(outside, os.path.join(self.code, "link.txt"))
        bad = ["../escape.py", "/etc/hostname", ".git/config", os.path.join(self.state, "checks", "T1.sh"), "link.txt", "sub/../../x.py"]
        self.attempt([A("write_file", path=p, content="x = 1\n") for p in bad] + [A("finish", summary="s")])
        self.assertFalse(os.path.exists(outside)); self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.py")))
        self.assertEqual(sum("ERROR" in r for r in self.results()), len(bad))

    def test_state_dir_reads_are_refused_too(self):
        self.attempt([A("read_file", path=os.path.join(self.state, "tasks.json")), A("finish", summary="s")])
        self.assertTrue(any("state directory" in r for r in self.results()))

class TestRun(Base):
    def test_denied_commands(self):
        cmds = ["sudo ls", "curl http://x", "git push origin main", "git reset --hard HEAD", "git commit -am x", f"cat {self.state}/tasks.json",
                "rm -rf /", "pip install requests", "echo hi > checks.lock"]
        self.attempt([A("run", command=c) for c in cmds] + [A("finish", summary="s")])
        self.assertEqual(sum("command refused" in r for r in self.results()), len(cmds))

    def test_allowed_commands_run(self):
        self.attempt([A("run", command="git status --short"), A("run", command="python3 -c 'print(6*7)'"), A("finish", summary="s")])
        rs = self.results(); self.assertTrue(any("exit 0" in r for r in rs)); self.assertTrue(any("42" in r for r in rs))

    def test_timeout_is_enforced(self):
        self.set_env(RUN_TIMEOUT="1"); self.attempt([A("run", command="sleep 5"), A("finish", summary="s")])
        self.assertTrue(any("exit 124" in r and "timed out" in r for r in self.results()))

    def test_long_output_is_truncated(self):
        self.attempt([A("run", command="python3 -c \"print('x'*20000)\""), A("finish", summary="s")])
        self.assertLess(max(len(r) for r in self.results()), 3300)

class TestActions(Base):
    def test_replan_add_task_and_forbidden_op(self):
        self.attempt([A("replan", op={"op": "add_task", "title": "extra", "check": "true", "reason": "needed"}),
                      A("replan", op={"op": "delete_task", "task": "T1", "reason": "nah"}), A("finish", summary="s")])
        ids = [t["id"] for t in T.TaskStore(self.proj()).load()["tasks"]]; self.assertEqual(ids, ["T1", "T2"])
        rs = self.results(); self.assertIn('"ok": true', rs[0]); self.assertIn('"ok": false', rs[1])

    def test_note_lesson_goes_to_the_raw_log(self):
        self.attempt([A("note_lesson", text="the add function must handle floats too"), A("note_lesson", text="short"), A("finish", summary="s")])
        raw = open(os.path.join(self.home, "memory", "lessons.raw.jsonl")).read().splitlines()
        self.assertEqual(len(raw), 1); rec = json.loads(raw[0]); self.assertEqual((rec["task"], rec["project"]), ("T1", "t1"))

    def test_list_dir_and_read_offset(self):
        self.write_code("big.txt", "\n".join(f"line{i}" for i in range(1, 500)))
        self.attempt([A("list_dir", path="."), A("read_file", path="big.txt", offset=300, limit=2), A("finish", summary="s")])
        rs = self.results(); self.assertIn("big.txt", rs[0]); self.assertIn("300: line300", rs[1]); self.assertNotIn("line301", rs[1].split("more lines")[0].split("301: ")[0] + "")

class TestLoopControl(Base):
    def test_identical_actions_are_stopped(self):
        out = self.attempt([A("list_dir", path=".")] * 8); self.assertEqual(out["ended"], "stuck repeating the same action")

    def test_turn_limit(self):
        self.set_env(WORKER_MAX_TURNS="3"); out = self.attempt([A("list_dir", path="."), A("list_dir", path="src"), A("read_file", path="README.md"), A("list_dir", path="x")])
        self.assertEqual(out["ended"], "turn limit"); self.assertEqual(out["turns"], 3)

    def test_alternate_variant_and_model_override(self):
        self.attempt([A("finish", summary="s")], variant="alt", model="big-model", temperature=0.8)
        r = self.fake.requests[0]; self.assertEqual(r["model"], "big-model"); self.assertEqual(r["options"]["temperature"], 0.8)
        self.assertIn("DIFFERENT", r["messages"][0]["content"])

    def test_flat_schema_fallback_when_server_rejects_anyof(self):
        out = self.attempt([A("write_file", path="a.py", content=ADD), A("finish", summary="s")], fake={"reject_schema": True})
        self.assertEqual(self.file("a.py"), ADD); self.assertIn("anyOf", self.fake.requests[0]["format"]); self.assertNotIn("anyOf", self.fake.requests[1]["format"])

    def test_unreachable_model_ends_cleanly(self):
        self.set_env(WORKER="native", OLLAMA_PORT="1"); p = self.proj(); t = T.TaskStore(p).load()["tasks"][0]
        import codebot.ollama as O; old = O.time.sleep; O.time.sleep = lambda s: None
        try: out = worker.run_attempt(p, t, 1, "")
        finally: O.time.sleep = old
        self.assertTrue(out["ended"].startswith("model error"))

    def test_history_is_compacted_inside_the_window(self):
        self.write_code("big.txt", "\n".join("x" * 80 for _ in range(300)))
        self.set_env(WORKER_CONTEXT_TOKENS="1500", WORKER_MAX_TURNS="14")
        self.attempt([A("read_file", path="big.txt", offset=i * 40 + 1, limit=40) for i in range(14)])
        sizes = [sum(len(m["content"]) for m in r["messages"]) / 3.5 for r in self.fake.requests]
        self.assertLess(max(sizes), 1500 * 2.2); first = self.fake.requests[-1]["messages"][1]["content"]; self.assertIn("EARLIER STEPS", first)

class TestPacker(Base):
    def test_includes_named_files_error_and_decisions(self):
        self.write_code("a.py", "def add(a, b):\n    return a - b\n"); self.write_code("other.py", "def unrelated():\n    return 'SECRET_UNRELATED_BODY'\n")
        with open(os.path.join(self.state, "GRILL.md"), "w") as f: f.write("# Decisions\n- ints only (user said so)\n# Assumptions\n- py3.12\n")
        task = T.TaskStore(self.proj()).load()["tasks"][0]
        text, man = packer.pack(self.proj(), task, "AssertionError: add is wrong")
        self.assertIn("1: def add(a, b):", text); self.assertIn("AssertionError: add is wrong", text); self.assertIn("ints only", text)
        self.assertIn("other.py", text); self.assertIn("unrelated:1", text); self.assertNotIn("SECRET_UNRELATED_BODY", text)
        self.assertEqual(man["files_shown"], ["a.py"]); self.assertIn("RELEVANT FILES", [n for n, _ in man["sections"]])

    def test_stays_within_budget_on_a_big_repo(self):
        for i in range(300): self.write_code(f"pkg/mod{i}.py", "\n".join(f"def f{i}_{j}(): pass" for j in range(30)))
        self.write_code("a.py", "x=1\n" * 400)
        task = T.TaskStore(self.proj()).load()["tasks"][0]
        text, man = packer.pack(self.proj(), task, "e" * 5000, budget_tokens=2500)
        self.assertLessEqual(man["tokens"], 2500); self.assertIn("write add in a.py", text); self.assertIn("ACCEPTANCE CHECK", text)

    def test_fixture_file_is_shown_to_the_worker(self):
        os.makedirs(os.path.join(self.state, "checks"), exist_ok=True)
        with open(os.path.join(self.state, "checks", "t1_test.py"), "w") as f: f.write("assert 1 == 1  # FIXTURE_MARKER\n")
        from codebot import planops as P
        P.apply(self.proj(), {"op": "add_task", "title": "second", "check": "python3 $CODEBOT_CHECKS/t1_test.py", "reason": "r"})
        task = T.get(T.TaskStore(self.proj()).load(), "T2")
        text, _ = packer.pack(self.proj(), task); self.assertIn("FIXTURE_MARKER", text)

    def test_symbol_maps(self):
        self.write_code("m.py", "class A:\n    def go(self): pass\nasync def run(): pass\n"); self.write_code("s.sh", "build() {\n  :\n}\n"); self.write_code("w.js", "export function hello() {}\nconst arrow = (x) => x\n")
        self.assertEqual(packer.symbols(self.code, "m.py"), ["A:1", "go:2", "run:3"]); self.assertEqual(packer.symbols(self.code, "s.sh"), ["build:1"])
        self.assertEqual(packer.symbols(self.code, "w.js"), ["hello:1", "arrow:2"])

class TestModelSplitter(Base):
    def test_model_split_is_used_when_valid(self):
        self.model([{"subtasks": [{"title": "stub", "check": "test -f a.py"}, {"title": "impl", "check": "python3 -c 'import a; a.add'"}]}])
        self.set_env(WORKER="native")
        r = sizing.auto_split(self.proj(), "T1"); self.assertTrue(r["ok"], r); self.assertEqual(r["children"], ["T1.1", "T1.2"])
        self.assertFalse(self.fake.requests[0]["think"])

    def test_garbage_from_the_model_falls_back_to_mechanical(self):
        planops.apply(self.proj(), {"op": "add_task", "title": "two files", "check": "test -f p.py && test -f q.py", "reason": "r"})
        self.model(["not json"]); r = sizing.auto_split(self.proj(), "T2"); self.assertTrue(r["ok"], r)

if __name__ == "__main__": unittest.main()
