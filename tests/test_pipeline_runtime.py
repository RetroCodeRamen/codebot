"""Pipeline scheduler, messaging, scope, design — fake model only."""
import json, os, shutil, tempfile, threading, time, unittest
from codebot.config import Project
from codebot.pipeline import design, mailbox, runtime as R, schedule, scope, validate_project
from codebot.pipeline.load import load_project
from codebot.pipeline.validate import ValidationError
from codebot import jobs

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
T = os.path.join(REPO, "pipelines", "templates")

def _home():
    d = tempfile.mkdtemp(prefix="cb-pipe-")
    os.makedirs(os.path.join(d, "config"))
    os.makedirs(os.path.join(d, "projects"))
    os.makedirs(os.path.join(d, "pipelines"))
    shutil.copytree(os.path.join(REPO, "pipelines", "templates"), os.path.join(d, "pipelines", "templates"))
    with open(os.path.join(d, "config", "codebot.env"), "w") as f:
        f.write('CODEBOT_MODEL="fake"\nOLLAMA_PORT=1\nTHERMAL_GUARD=0\n')
    return d

def _proj(home, name, bundle_path, spec=""):
    pd = os.path.join(home, "projects", name); os.makedirs(pd)
    code = os.path.join(home, "code", name); os.makedirs(code)
    if spec:
        with open(os.path.join(code, "SPEC.md"), "w") as f: f.write(spec)
    with open(os.path.join(pd, "project.env"), "w") as f:
        f.write(f'WORKDIR="{code}"\nRUNDIR="{code}"\nWORKFLOW="pipeline"\nPIPELINE="pipeline/project.json"\nPIPELINE_AUTO_APPROVE=1\n')
    design.write_project_pipeline(Project(name, home), load_project(bundle_path), template_meta={"template": "test"})
    return Project(name, home)

SPEC = """# Calc

## Requirements

1. Add two numbers. `add(2, 3)` is `5`. `mathx.py` provides `add(a, b)`.
"""

class Script:
    """Queue of JSON actions keyed by member id (FIFO per member)."""
    def __init__(self, plan):
        self.plan = {k: list(v) for k, v in plan.items()}
        self.calls = []
        self.lock = threading.Lock()
        self.concurrent = 0
        self.max_concurrent = 0

    def __call__(self, proj, messages, schema=None, model=None, temperature=None, num_predict=None):
        with self.lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            time.sleep(0.01)  # widen race window for serialization test
            sys_msg = messages[0]["content"]
            member = None
            for mid in list(self.plan):
                if f"`{mid}`" in sys_msg: member = mid; break
            if member is None:
                member = next(iter(self.plan))
            self.calls.append(member)
            if not self.plan[member]:
                return {"content": json.dumps({"action": "finish", "reason": "empty script"})}
            return {"content": json.dumps(self.plan[member].pop(0))}
        finally:
            with self.lock:
                self.concurrent -= 1

class TestScope(unittest.TestCase):
    def test_write_denied(self):
        agent = {"sees": {"read": ["src/**"], "write": ["src/**"]}}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValidationError):
                scope.write_text(d, agent, "secrets/x", "no")

class TestSolo(unittest.TestCase):
    def test_one_agent_completes(self):
        home = _home()
        try:
            proj = _proj(home, "solo1", os.path.join(home, "pipelines", "templates", "projects", "basic-calculator.json"), SPEC)
            script = Script({"dev": [
                {"action": "write", "path": "mathx.py", "content": "def add(a,b): return a+b\n"},
                {"action": "finish", "reason": "done"}
            ]})
            run, events = schedule.run_until_idle(proj, chat_fn=script, max_steps=40)
            self.assertEqual(run["status"], "COMPLETED", events)
            self.assertEqual(script.max_concurrent, 1)
            self.assertTrue(os.path.isfile(os.path.join(proj.workdir, "mathx.py")))
        finally:
            shutil.rmtree(home, ignore_errors=True)

class TestQA(unittest.TestCase):
    def test_blocking_question(self):
        home = _home()
        try:
            # two-member mini bundle
            bundle = {
                "project": {"schema": "codebot.pipeline/v1", "kind": "project", "id": "qa", "name": "qa"},
                "agents": {
                    "a": {"schema": "codebot.pipeline/v1", "kind": "agent", "id": "a", "name": "A", "goal": "ask",
                          "instructions": "Ask B then finish.", "memory": "task",
                          "sees": {"read": ["**"], "write": ["**"]}, "can_talk_to": [], "messages": ["QUESTION", "ANSWER", "TASK"]},
                    "b": {"schema": "codebot.pipeline/v1", "kind": "agent", "id": "b", "name": "B", "goal": "answer",
                          "instructions": "Answer questions.", "memory": "none",
                          "sees": {"read": ["**"], "write": []}, "can_talk_to": [], "messages": ["QUESTION", "ANSWER"]}
                },
                "team": {"schema": "codebot.pipeline/v1", "kind": "team", "id": "t", "name": "t", "members": [
                    {"id": "alice", "agent": "a", "can_talk_to": ["bob"]},
                    {"id": "bob", "agent": "b", "can_talk_to": ["alice"]}
                ]},
                "workflow": {"schema": "codebot.pipeline/v1", "kind": "workflow", "id": "w", "name": "w",
                             "nodes": [
                                 {"id": "start", "type": "start"},
                                 {"id": "run_a", "type": "run", "agent": "alice"},
                                 {"id": "end", "type": "end"}
                             ],
                             "edges": [{"from": "start", "to": "run_a"}, {"from": "run_a", "to": "end"}]}
            }
            validate_project(bundle)
            pd = os.path.join(home, "projects", "qa"); os.makedirs(pd); os.makedirs(os.path.join(home, "code", "qa"))
            with open(os.path.join(pd, "project.env"), "w") as f:
                f.write(f'WORKDIR="{home}/code/qa"\nRUNDIR="{home}/code/qa"\nWORKFLOW="pipeline"\nPIPELINE="pipeline/project.json"\n')
            design.write_project_pipeline(Project("qa", home), bundle)
            proj = Project("qa", home)
            script = Script({
                "alice": [
                    {"action": "ask", "to": "bob", "text": "what is 2+2?"},
                    {"action": "finish", "reason": "got answer"}
                ],
                "bob": [
                    {"action": "answer", "text": "4"}
                ]
            })
            run, events = schedule.run_until_idle(proj, chat_fn=script, max_steps=40)
            self.assertEqual(run["status"], "COMPLETED", events)
            self.assertEqual(script.calls[:3], ["alice", "bob", "alice"])
            self.assertEqual(script.max_concurrent, 1)
            msgs = list(mailbox.iter_messages(R.pipeline_dir(proj)))
            self.assertTrue(any(m["type"] == "QUESTION" for m in msgs))
            self.assertTrue(any(m["type"] == "ANSWER" for m in msgs))
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_nested_abc(self):
        home = _home()
        try:
            bundle = {
                "project": {"schema": "codebot.pipeline/v1", "kind": "project", "id": "nest", "name": "nest"},
                "agents": {x: {"schema": "codebot.pipeline/v1", "kind": "agent", "id": x, "name": x, "goal": x,
                               "instructions": x, "memory": "task", "sees": {"read": ["**"], "write": ["**"]},
                               "can_talk_to": [], "messages": ["QUESTION", "ANSWER", "TASK"]} for x in ("a", "b", "c")},
                "team": {"schema": "codebot.pipeline/v1", "kind": "team", "id": "t", "name": "t", "members": [
                    {"id": "A", "agent": "a", "can_talk_to": ["B"]},
                    {"id": "B", "agent": "b", "can_talk_to": ["A", "C"]},
                    {"id": "C", "agent": "c", "can_talk_to": ["B"]}
                ]},
                "workflow": {"schema": "codebot.pipeline/v1", "kind": "workflow", "id": "w", "name": "w",
                             "nodes": [{"id": "start", "type": "start"}, {"id": "run", "type": "run", "agent": "A"},
                                       {"id": "end", "type": "end"}],
                             "edges": [{"from": "start", "to": "run"}, {"from": "run", "to": "end"}]}
            }
            validate_project(bundle)
            pd = os.path.join(home, "projects", "nest"); os.makedirs(pd); os.makedirs(os.path.join(home, "code", "nest"))
            with open(os.path.join(pd, "project.env"), "w") as f:
                f.write(f'WORKDIR="{home}/code/nest"\nRUNDIR="{home}/code/nest"\nWORKFLOW="pipeline"\nPIPELINE="pipeline/project.json"\n')
            design.write_project_pipeline(Project("nest", home), bundle)
            proj = Project("nest", home)
            script = Script({
                "A": [{"action": "ask", "to": "B", "text": "ask B"}, {"action": "finish"}],
                "B": [{"action": "ask", "to": "C", "text": "ask C"}, {"action": "answer", "text": "from B"}],
                "C": [{"action": "answer", "text": "from C"}]
            })
            run, events = schedule.run_until_idle(proj, chat_fn=script, max_steps=50)
            self.assertEqual(run["status"], "COMPLETED", events)
            # A, B, C, B, A order
            self.assertEqual(script.calls, ["A", "B", "C", "B", "A"])
            self.assertEqual(script.max_concurrent, 1)
        finally:
            shutil.rmtree(home, ignore_errors=True)

class TestRestart(unittest.TestCase):
    def test_run_json_restored(self):
        home = _home()
        try:
            proj = _proj(home, "rs", os.path.join(home, "pipelines", "templates", "projects", "basic-calculator.json"), SPEC)
            script = Script({"dev": [{"action": "finish"}]})
            schedule.step(proj, chat_fn=script)  # start
            schedule.step(proj, chat_fn=script)  # maybe more
            path = R.run_path(proj)
            self.assertTrue(os.path.isfile(path))
            run1 = R.load_run(proj)
            run2 = R.load_run(proj)
            self.assertEqual(run1["created"], run2["created"])
        finally:
            shutil.rmtree(home, ignore_errors=True)

class TestDesign(unittest.TestCase):
    def test_heuristic_solo(self):
        home = _home()
        try:
            bundle, meta = design.propose(home, SPEC)
            self.assertEqual(meta["template"], "solo")
            validate_project(bundle)
        finally:
            shutil.rmtree(home, ignore_errors=True)

class TestReviewFanout(unittest.TestCase):
    def test_architect_then_three_reviewers(self):
        home = _home()
        try:
            proj = _proj(home, "rev", os.path.join(home, "pipelines", "templates", "projects", "spec-with-reviewers.json"), SPEC)
            # Bound the loop tightly via env already; script each role
            script = Script({
                "elephant": [
                    {"action": "write", "path": "SPEC.md", "content": SPEC},
                    {"action": "finish", "reason": "drafted"},
                    {"action": "finish", "reason": "merged"},  # after join revise
                ],
                "gf-security": [{"action": "findings", "findings": [{"where": "SPEC", "what": "ok", "serious": "low"}]}],
                "gf-structure": [{"action": "findings", "findings": [{"where": "SPEC", "what": "examples ok", "serious": "low"}]}],
                "gf-ui": [{"action": "findings", "findings": [{"where": "SPEC", "what": "no UI", "serious": "low"}]}],
            })
            run, events = schedule.run_until_idle(proj, chat_fn=script, max_steps=80)
            self.assertEqual(run["status"], "COMPLETED", events[-15:])
            self.assertEqual(script.max_concurrent, 1)
            # all three goldfish ran
            self.assertTrue(set(script.calls) >= {"elephant", "gf-security", "gf-structure", "gf-ui"})
        finally:
            shutil.rmtree(home, ignore_errors=True)

class TestJobsKind(unittest.TestCase):
    def test_pipeline_kind_described(self):
        self.assertIn("pipeline", jobs.KINDS)
        self.assertIn("pipeline", jobs.describe({"kind": "pipeline", "name": "x", "pid": 1}))

class TestPodsAndDebate(unittest.TestCase):
    def test_small_pods_template_loads(self):
        home = _home()
        try:
            from codebot.pipeline import context as C
            bundle = load_project(os.path.join(home, "pipelines", "templates", "projects", "small-pods.json"))
            self.assertEqual(len(bundle["team"]["pods"]), 2)
            validate_project(bundle)
            self.assertEqual(C.pod_of(bundle, "math-dev")["id"], "math")
            self.assertEqual(C.manager_of(bundle, "ui-dev"), "sync")
            self.assertIsNone(C.manager_of(bundle, "sync"))
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_goldfish_may_ask_each_other(self):
        home = _home()
        try:
            proj = _proj(home, "deb", os.path.join(home, "pipelines", "templates", "projects", "spec-debate.json"), SPEC)
            script = Script({
                "elephant": [
                    {"action": "write", "path": "SPEC.md", "content": SPEC},
                    {"action": "finish"},
                    {"action": "finish"},
                ],
                "gf-security": [
                    {"action": "ask", "to": "gf-structure", "text": "do examples cover auth?"},
                    {"action": "findings", "findings": [{"where": "SPEC", "what": "ok after debate", "serious": "low"}]},
                ],
                "gf-structure": [
                    {"action": "answer", "text": "yes for R1"},
                    {"action": "findings", "findings": [{"where": "SPEC", "what": "ok", "serious": "low"}]},
                ],
                "gf-ui": [
                    {"action": "findings", "findings": [{"where": "SPEC", "what": "no UI", "serious": "low"}]},
                ],
            })
            run, events = schedule.run_until_idle(proj, chat_fn=script, max_steps=100)
            self.assertEqual(run["status"], "COMPLETED", events[-20:])
            self.assertEqual(script.max_concurrent, 1)
            msgs = list(mailbox.iter_messages(R.pipeline_dir(proj)))
            self.assertTrue(any(m["type"] == "QUESTION" and m["from"] == "gf-security" and m["to"] == "gf-structure" for m in msgs))
            self.assertTrue(any(m["type"] == "ANSWER" for m in msgs))
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_drafts_bridge_calls_drafts_step(self):
        home = _home()
        try:
            proj = _proj(home, "br", os.path.join(home, "pipelines", "templates", "projects", "basic-calculator.json"), SPEC)
            calls = []
            def fake_step(p):
                calls.append(p.name)
                return {"event": "all_done"}
            import codebot.drafts as drafts
            real = drafts.step
            drafts.step = fake_step
            try:
                out = None
                for _ in range(5):
                    out = schedule.step(proj)  # no chat_fn → drafts bridge on run node
                    if out.get("event") == "drafts":
                        break
                self.assertEqual(calls, ["br"], out)
                self.assertEqual(out.get("event"), "drafts")
                self.assertEqual(out["result"]["event"], "all_done")
            finally:
                drafts.step = real
        finally:
            shutil.rmtree(home, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()

class TestLinksMemoryCancel(unittest.TestCase):
    def test_link_kinds_gate_talk_and_repo(self):
        from codebot.pipeline import context as C
        agent = {"can_talk_to": [], "links": [
            {"kind": "agent", "to": "b"},
            {"kind": "repository", "to": "c"},
            {"kind": "both", "to": "d"},
        ]}
        self.assertTrue(C.may_talk(agent, "b"))
        self.assertFalse(C.may_talk(agent, "c"))
        self.assertTrue(C.may_talk(agent, "d"))
        self.assertFalse(C.may_share_repo(agent, "b"))
        self.assertTrue(C.may_share_repo(agent, "c"))
        self.assertTrue(C.may_share_repo(agent, "d"))
        # can_talk_to still works without links
        self.assertTrue(C.may_talk({"can_talk_to": ["z"], "links": []}, "z"))

    def test_memory_none_hides_findings_from_inbox(self):
        from codebot.pipeline import context as C
        home = _home()
        try:
            proj = _proj(home, "mem", os.path.join(home, "pipelines", "templates", "projects", "spec-with-reviewers.json"), SPEC)
            rd = R.pipeline_dir(proj); os.makedirs(rd, exist_ok=True)
            mailbox.send(rd, "gf-structure", "gf-security", "FINDING", {"findings": [{"what": "secret"}]})
            mailbox.send(rd, "elephant", "gf-security", "QUESTION", {"text": "need review?"})
            bundle, _ = R.load_bundle(proj)
            msgs, _ = C.build_messages(bundle, rd, "gf-security", "review")
            blob = msgs[-1]["content"]
            self.assertIn("QUESTION", blob)
            self.assertNotIn("FINDING", blob)
            self.assertNotIn("secret", blob)
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_cancel_marks_run(self):
        home = _home()
        try:
            proj = _proj(home, "cx", os.path.join(home, "pipelines", "templates", "projects", "basic-calculator.json"), SPEC)
            schedule.step(proj)  # create run
            out = schedule.cancel(proj)
            self.assertEqual(out["status"], "CANCELLED")
            self.assertEqual(R.load_run(proj)["status"], "CANCELLED")
            # second cancel is idempotent
            self.assertEqual(schedule.cancel(proj)["status"], "CANCELLED")
        finally:
            shutil.rmtree(home, ignore_errors=True)

class TestInitPodsNested(unittest.TestCase):
    def test_init_installs_template(self):
        home = _home()
        try:
            # drafts-like project without pipeline yet
            pd = os.path.join(home, "projects", "up"); os.makedirs(pd)
            code = os.path.join(home, "code", "up"); os.makedirs(code)
            with open(os.path.join(code, "SPEC.md"), "w") as f: f.write(SPEC)
            with open(os.path.join(pd, "project.env"), "w") as f:
                f.write(f'WORKDIR="{code}"\nRUNDIR="{code}"\nWORKFLOW="drafts"\n')
            with open(os.path.join(pd, "SPEC.md"), "w") as f: f.write(SPEC)
            proj = Project("up", home)
            path, meta = design.init_project(proj, "solo")
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(meta["template"], "solo")
            with open(proj.p("project.env")) as ef:
                self.assertIn('WORKFLOW="pipeline"', ef.read())
            with self.assertRaises(ValidationError):
                design.init_project(proj, "solo")  # exists
            path2, meta2 = design.init_project(proj, "debate", force=True)
            self.assertEqual(meta2["template"], "debate")
            self.assertTrue(os.path.isfile(path2))
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_small_pods_run_completes(self):
        home = _home()
        try:
            proj = _proj(home, "pods", os.path.join(home, "pipelines", "templates", "projects", "small-pods.json"), SPEC)
            script = Script({
                "sync": [
                    {"action": "write", "path": "SPEC.md", "content": SPEC},
                    {"action": "finish"},
                ],
                "math-dev": [
                    {"action": "write", "path": "mathx.py", "content": "def add(a,b): return a+b\n"},
                    {"action": "finish"},
                ],
                "ui-dev": [
                    {"action": "write", "path": "ui.py", "content": "def show(x): return str(x)\n"},
                    {"action": "finish"},
                ],
            })
            run, events = schedule.run_until_idle(proj, chat_fn=script, max_steps=80)
            self.assertEqual(run["status"], "COMPLETED", events[-20:])
            msgs = list(mailbox.iter_messages(R.pipeline_dir(proj)))
            self.assertTrue(any(m["type"] == "RESULT" and m["to"] == "sync" for m in msgs), msgs[-10:])
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_nested_team_expands(self):
        home = _home()
        try:
            bundle = load_project(os.path.join(home, "pipelines", "templates", "projects", "nested-pods.json"))
            ids = {m["id"] for m in bundle["team"]["members"]}
            self.assertIn("sync", ids)
            self.assertIn("math", ids)
            self.assertIn("math-impl", ids)
            self.assertIn("math-check", ids)
            self.assertTrue(any(p.get("manager") == "math" for p in bundle["team"]["pods"]))
            validate_project(bundle)
            proj = _proj(home, "nest", os.path.join(home, "pipelines", "templates", "projects", "nested-pods.json"), SPEC)
            script = Script({
                "sync": [{"action": "write", "path": "SPEC.md", "content": SPEC}, {"action": "finish"}],
                "math-impl": [{"action": "write", "path": "mathx.py", "content": "def add(a,b): return a+b\n"}, {"action": "finish"}],
                "math-check": [{"action": "findings", "findings": [{"where": "mathx.py", "what": "ok", "serious": "low"}]}],
            })
            # findings completes check node; impl finish notifies math manager
            run, events = schedule.run_until_idle(proj, chat_fn=script, max_steps=80)
            self.assertEqual(run["status"], "COMPLETED", events[-25:])
        finally:
            shutil.rmtree(home, ignore_errors=True)

class TestApproveGate(unittest.TestCase):
    def test_waiting_approval_then_approve(self):
        home = _home()
        try:
            proj = _proj(home, "ap", os.path.join(home, "pipelines", "templates", "projects", "spec-with-reviewers.json"), SPEC)
            # force human approve
            with open(proj.p("project.env"), "a") as f: f.write('PIPELINE_AUTO_APPROVE="0"\n')
            proj.cfg["PIPELINE_AUTO_APPROVE"] = "0"
            script = Script({
                "elephant": [
                    {"action": "write", "path": "SPEC.md", "content": SPEC},
                    {"action": "finish"},
                    {"action": "finish"},
                ],
                "gf-security": [{"action": "findings", "findings": [{"where": "SPEC", "what": "ok", "serious": "low"}]}],
                "gf-structure": [{"action": "findings", "findings": [{"where": "SPEC", "what": "ok", "serious": "low"}]}],
                "gf-ui": [{"action": "findings", "findings": [{"where": "SPEC", "what": "ok", "serious": "low"}]}],
            })
            run, events = schedule.run_until_idle(proj, chat_fn=script, max_steps=80)
            self.assertEqual(run["status"], "WAITING_APPROVAL", events[-15:])
            ev = schedule.approve(proj)
            # may need more steps after approve to finish gates
            run2, events2 = schedule.run_until_idle(proj, chat_fn=script, max_steps=40)
            self.assertEqual(run2["status"], "COMPLETED", (ev, events2[-10:]))
        finally:
            shutil.rmtree(home, ignore_errors=True)

class TestPersistentMemory(unittest.TestCase):
    def test_project_memory_packs_lessons(self):
        from codebot.pipeline import context as C
        home = _home()
        try:
            proj = _proj(home, "mem2", os.path.join(home, "pipelines", "templates", "projects", "basic-calculator.json"), SPEC)
            md = os.path.join(home, "memory"); os.makedirs(md, exist_ok=True)
            with open(os.path.join(md, "lessons.raw.jsonl"), "w") as f:
                f.write(json.dumps({"id": "L1", "text": "always write add with two args", "task": "calc", "project": "x", "ts": 1}) + "\n")
            with open(os.path.join(md, "lessons.status.json"), "w") as f:
                json.dump({"lessons": {"L1": {"used": 2, "pass": 2, "pruned": False, "sources": []}}, "baseline": {"n": 2, "pass": 2}}, f)
            with open(os.path.join(md, "lessons.md"), "w") as f: f.write("# Lessons\n- always write add with two args\n")
            bundle, _ = R.load_bundle(proj)
            # architect has memory project; reuse solo agent with override
            bundle["agents"]["solo-developer"]["memory"] = "persistent"
            rd = R.pipeline_dir(proj); os.makedirs(rd, exist_ok=True)
            msgs, _ = C.build_messages(bundle, rd, "dev", "always write the program with examples", proj=proj)
            self.assertIn("Lessons:", msgs[-1]["content"])
            self.assertIn("always write", msgs[-1]["content"])
        finally:
            shutil.rmtree(home, ignore_errors=True)
