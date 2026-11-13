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

def _proj(home, name, bundle_path, spec="", check=None):
    pd = os.path.join(home, "projects", name); os.makedirs(pd)
    code = os.path.join(home, "code", name); os.makedirs(code)
    if spec:
        with open(os.path.join(code, "SPEC.md"), "w") as f: f.write(spec)
    with open(os.path.join(pd, "project.env"), "w") as f:
        f.write(f'WORKDIR="{code}"\nRUNDIR="{code}"\nWORKFLOW="pipeline"\nPIPELINE="pipeline/project.json"\nPIPELINE_AUTO_APPROVE=1\n')
    bundle = load_project(bundle_path)
    for n in bundle["workflow"]["nodes"]:
        if check and n.get("type") == "gate": n["check"] = check
    design.write_project_pipeline(Project(name, home), bundle, template_meta={"template": "test"})
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
                if f"You are member `{mid}`" in sys_msg: member = mid; break          # not any mention: a worker's prompt names its manager too
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

class TestGatesRunTheChecks(unittest.TestCase):
    """A gate passes on what the harness ran, never on a file being there or on an agent saying it is done."""
    CALC = ("pipelines", "templates", "projects", "basic-calculator.json")      # build -> gate, the gate's fail edge goes back to build
    PODS = ("pipelines", "templates", "projects", "small-pods.json")            # the gate has a pass edge only

    def _run(self, name, tpl, dev, check=None, files=None, spec=SPEC):
        home = _home(); self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        proj = _proj(home, name, os.path.join(home, *tpl), spec, check=check)
        for n, text in (files or {}).items():
            with open(os.path.join(proj.workdir, n), "w") as f: f.write(text)
        script = Script({"dev": dev, "sync": [{"action": "finish"}], "math-dev": dev, "ui-dev": [{"action": "finish"}]})
        run, events = schedule.run_until_idle(proj, chat_fn=script, max_steps=80)
        return proj, run, events

    def test_examples_gate_passes_code_that_does_what_the_spec_shows(self):
        proj, run, events = self._run("g1", self.CALC, [{"action": "write", "path": "mathx.py", "content": "def add(a, b): return a + b\n"}, {"action": "finish"}])
        self.assertEqual(run["status"], "COMPLETED", events)
        g = run["gates"]["check"]
        self.assertEqual((g["check"], g["result"], g["fails"]), ("examples", "pass", 0))
        self.assertIn("1 of 1 spec examples pass", g["detail"])
        self.assertEqual(sorted(os.listdir(proj.workdir)), ["SPEC.md", "mathx.py"])           # judged on a copy: nothing is left in the code dir

    def test_examples_gate_fails_wrong_code_and_stops_after_its_retries(self):
        proj, run, events = self._run("g2", self.CALC, [{"action": "write", "path": "mathx.py", "content": "def add(a, b): return a - b\n"}, {"action": "finish"}])
        self.assertEqual(run["status"], "FAILED", events)
        self.assertIn("gate check failed 3 times: 0 of 1 spec examples pass", run["error"])
        self.assertIn("add(2, 3)", run["gates"]["check"]["detail"])
        self.assertEqual([e.get("when") for e in events if e.get("event") == "gate"], ["fail"] * 3)

    def test_examples_gate_fails_when_no_code_was_written(self):
        proj, run, events = self._run("g3", self.CALC, [{"action": "finish", "reason": "all done, everything works"}])
        self.assertEqual(run["status"], "FAILED", events)
        self.assertIn("mathx.py", run["gates"]["check"]["detail"])

    def test_a_failed_gate_never_takes_its_pass_edge(self):
        proj, run, events = self._run("g4", self.PODS, [{"action": "write", "path": "mathx.py", "content": "def add(a, b): return 0\n"}, {"action": "finish"}])
        self.assertEqual(run["status"], "FAILED", events[-10:])
        self.assertTrue(run["error"].startswith("gate gate failed: 0 of 1 spec examples pass"), run["error"])

    def test_examples_gate_fails_a_spec_without_examples(self):
        proj, run, events = self._run("g5", self.PODS, [{"action": "finish"}], spec="# Calc\n\n## Requirements\n\n1. Add two numbers.\n")
        self.assertEqual(run["status"], "FAILED", events[-10:])
        self.assertIn("no example codebot can run", run["error"])

    def test_tests_gate_runs_verify_sh(self):
        ok = {"verify.sh": "python3 -c 'import mathx; assert mathx.add(2, 3) == 5'\n"}
        code = lambda body: [{"action": "write", "path": "mathx.py", "content": f"def add(a, b): return {body}\n"}, {"action": "finish"}]
        proj, run, events = self._run("t1", self.PODS, code("a + b"), check="tests", files=ok)
        self.assertEqual(run["status"], "COMPLETED", events[-10:])
        self.assertEqual(run["gates"]["gate"]["detail"], "verify.sh exited with status 0")
        proj, run, events = self._run("t2", self.PODS, code("a * b"), check="tests", files=ok)
        self.assertEqual(run["status"], "FAILED", events[-10:])
        self.assertIn("verify.sh exited with status 1", run["error"])
        self.assertIn("AssertionError", run["gates"]["gate"]["detail"])
        proj, run, events = self._run("t3", self.PODS, code("a + b"), check="tests")
        self.assertEqual(run["status"], "FAILED", events[-10:])
        self.assertIn("no verify.sh", run["error"])

    def test_an_unknown_check_is_refused_and_would_fail(self):
        home = _home(); self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        bundle = load_project(os.path.join(home, *self.PODS))
        next(n for n in bundle["workflow"]["nodes"] if n["type"] == "gate")["check"] = "vibes"
        with self.assertRaises(ValidationError): validate_project(bundle)
        from codebot.pipeline import gates
        proj = _proj(home, "u1", os.path.join(home, *self.PODS), SPEC)
        self.assertEqual(gates.run(proj, bundle, {"id": "gate", "check": "vibes"})[0], "fail")

TWO = SPEC + "2. Show a number. `ui.py` provides `show(x)`. `show(5)` is `'5'`.\n"

class Rec(Script):
    """The scripted model, keeping what each call was shown: (member, system text, user text)."""
    def __call__(self, proj, messages, **kw):
        out = super().__call__(proj, messages, **kw)
        self.seen = getattr(self, "seen", []) + [(self.calls[-1], messages[0]["content"], messages[-1]["content"], kw.get("schema"))]
        return out

GOOD = {"action": "write", "path": "mathx.py", "content": "def add(a, b): return a + b\n"}
BAD = {"action": "write", "path": "mathx.py", "content": "def add(a, b): return a - b\n"}
FIN = {"action": "finish"}

class TestAgentsDoWork(unittest.TestCase):
    """An agent can look, run and check, and its next turn starts from what its last one showed."""
    CALC = ("pipelines", "templates", "projects", "basic-calculator.json")
    REVIEW = ("pipelines", "templates", "projects", "spec-with-reviewers.json")

    def _run(self, name, dev, tpl=None, plan=None, steps=120):
        home = _home(); self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        proj = _proj(home, name, os.path.join(home, *(tpl or self.CALC)), SPEC)
        script = Rec(plan or {"dev": dev})
        run, events = schedule.run_until_idle(proj, chat_fn=script, max_steps=steps)
        return proj, run, events, script

    def test_each_turn_shows_what_the_turns_before_it_did(self):
        proj, run, events, s = self._run("w1", [{"action": "list"}, {"action": "read", "path": "SPEC.md"}, BAD, {"action": "check"}, GOOD, {"action": "check"}, FIN])
        self.assertEqual(run["status"], "COMPLETED", events)
        system, first = s.seen[0][1], s.seen[0][2]
        self.assertIn('{"action": "check"}', system); self.assertIn('{"action": "run", "command"', system)
        self.assertIn("Files in the code directory:\nSPEC.md", first); self.assertNotIn("Your turns on this task", first)
        self.assertIn("1. list\nSPEC.md (", s.seen[1][2])
        self.assertIn("2. read SPEC.md\nSPEC.md:\n```\n# Calc", s.seen[2][2])
        self.assertIn("3. write mathx.py (28 characters)\nwrote mathx.py", s.seen[3][2])
        after_check = s.seen[4][2]
        self.assertIn("4. check\nThe examples do not pass yet: 0 of 1 spec examples pass", after_check); self.assertIn("add(2, 3)", after_check)
        self.assertIn("6. check\nThe examples pass: 1 of 1 spec examples pass, 1 of 1 declared names defined. Nothing here is left to fix: finish.", s.seen[6][2])
        self.assertEqual(len(s.seen), 7)

    def test_run_shows_the_commands_output(self):
        proj, run, events, s = self._run("w2", [{"action": "run", "command": "python3 -c 'print(6 * 7)'"}, GOOD, FIN])
        self.assertEqual(run["status"], "COMPLETED", events)
        self.assertIn("1. run `python3 -c 'print(6 * 7)'`\nexit status 0\n42", s.seen[1][2])

    def test_a_request_that_cannot_be_done_is_a_turn_with_an_error_not_a_failed_run(self):
        proj, run, events, s = self._run("w3", [{"action": "read", "path": "../../etc/passwd"}, {"action": "read", "path": "nope.py"}, GOOD,
                                                {"action": "ask", "to": "nobody", "text": "?"}, GOOD, FIN])
        self.assertEqual(run["status"], "COMPLETED", events)
        self.assertIn("error: unsafe path", s.seen[1][2])
        self.assertIn("error: there is no file 'nope.py'", s.seen[2][2])
        self.assertIn("error: you may not use 'ask' here", s.seen[4][2])
        self.assertIn("error: mathx.py already has exactly this text, so there is nothing to write", s.seen[5][2])

    def test_an_agent_is_offered_only_the_actions_it_has_a_right_to(self):
        elephant = [{"action": "run", "command": "id"}, {"action": "write", "path": "mathx.py", "content": "x"}, {"action": "write", "path": "SPEC.md", "content": SPEC}, FIN, FIN]
        finding = [{"action": "findings", "findings": [{"where": "SPEC", "what": "ok", "serious": "low"}]}]
        proj, run, events, s = self._run("w4", None, tpl=self.REVIEW, plan={"elephant": elephant, "gf-security": finding, "gf-structure": finding, "gf-ui": finding})
        self.assertEqual(run["status"], "COMPLETED", events[-10:])
        self.assertEqual(s.seen[0][3]["properties"]["action"]["enum"], ["list", "read", "write", "ask", "escalate", "findings", "finish", "fail"])
        self.assertIn("error: you may not use 'run' here", s.seen[1][2])
        self.assertIn("error: write denied: mathx.py", s.seen[2][2])
        self.assertFalse(os.path.exists(os.path.join(proj.workdir, "mathx.py")))

    def test_a_cut_off_or_unusable_reply_is_a_turn_too(self):
        home = _home(); self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        proj = _proj(home, "w5", os.path.join(home, *self.CALC), SPEC)
        replies = [{"content": '{"action": "write", "path": "mathx.py", "content": "def add(a, b', "done_reason": "length"}, {"content": "sorry"},
                   {"content": json.dumps(GOOD)}, {"content": json.dumps(FIN)}]
        shown = []
        def chat(p, messages, **kw): shown.append(messages[-1]["content"]); return replies.pop(0)
        run, events = schedule.run_until_idle(proj, chat_fn=chat, max_steps=40)
        self.assertEqual(run["status"], "COMPLETED", events)
        self.assertIn("your reply was cut off after 4000 tokens", shown[1]); self.assertIn("your reply could not be used: not JSON", shown[2])

    def test_an_agent_that_never_finishes_runs_out_of_turns_and_the_gate_decides(self):
        loop = [{"action": "run", "command": f"echo {i}"} for i in range(40)]
        proj, run, events, s = self._run("w6", loop, steps=200)
        self.assertEqual(run["status"], "FAILED", events[-5:])
        self.assertIn("gate check failed 3 times", run["error"])
        self.assertEqual(run["warnings"], ["dev on node build: it used all 16 turns without finishing"] * 2)       # the third visit ends the script
        self.assertEqual([o["outcome"] for o in run["outcomes"]], ["exhausted_turns", "exhausted_turns", "completed"])
        self.assertEqual(len(s.seen), 41)                          # 16 + 16, then the 8 left of the script and its "finish"

    def test_looking_again_at_what_has_not_changed_is_refused(self):
        proj, run, events, s = self._run("w8", [BAD, {"action": "read", "path": "mathx.py"}, {"action": "check"}, {"action": "read", "path": "mathx.py"}, {"action": "check"},
                                                {"action": "list"}, {"action": "list"}, GOOD, FIN])
        self.assertEqual(run["status"], "COMPLETED", events)
        self.assertIn("2. read mathx.py\nmathx.py:\n```\ndef add", s.seen[2][2])                 # the first read and check are answered
        self.assertIn("3. check\nThe examples do not pass yet", s.seen[3][2])
        self.assertIn("error: you read mathx.py on turn 2 and it has not changed since", s.seen[4][2])
        self.assertIn("error: no file has changed since your check on turn 3, so the result is the same: The examples do not pass yet", s.seen[5][2])
        self.assertIn("6. list\nSPEC.md", s.seen[6][2])
        self.assertIn("error: no file has changed since you listed them on turn 6", s.seen[7][2])
        self.assertNotIn("warnings", run)

    def test_after_a_passing_check_the_first_refused_turn_ends_the_node(self):
        proj, run, events, s = self._run("w9", [GOOD, {"action": "check"}, {"action": "read", "path": "SPEC.md"}, {"action": "check"}, {"action": "list"}, FIN])
        self.assertEqual(run["status"], "COMPLETED", events)
        self.assertEqual(len(s.seen), 4)                               # write, check, read, the refused check: no fifth call
        self.assertNotIn("warnings", run)                              # its part was done; nothing to warn about
        self.assertEqual(events[[e.get("event") for e in events].index("run_done") - 1]["result"]["reason"], "its check passed and it had nothing new to look at")

    def test_three_refused_turns_in_a_row_end_the_node_with_a_warning(self):
        proj, run, events, s = self._run("w11", [BAD, {"action": "check"}] + [{"action": "check"}] * 5)
        self.assertEqual(run["status"], "FAILED", events[-4:])
        self.assertEqual(run["warnings"][0], "dev on node build: it was stopped after 3 refused turns in a row without finishing")
        self.assertEqual(run["outcomes"][0]["outcome"], "exhausted_refusals")
        self.assertEqual([m for m, *_ in s.seen[:5]], ["dev"] * 5); self.assertIn("gate check failed", run["error"])

    def test_a_workers_check_is_about_its_own_files(self):
        from codebot.pipeline import tools
        home = _home(); self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        proj = _proj(home, "w10", os.path.join(home, *self.CALC), TWO)
        with open(os.path.join(proj.workdir, "mathx.py"), "w") as f: f.write("def add(a, b): return a + b\n")
        mine = {"sees": {"read": ["**"], "write": ["mathx.py"]}}; other = {"sees": {"read": ["**"], "write": ["ui.py"]}}
        self.assertIn("The examples pass: 1 of 1 spec examples for your files (mathx.py) pass, 1 of 1 declared names defined", tools.check(proj, proj.workdir, mine))
        self.assertIn("do not pass yet: 0 of 1 spec examples for your files (ui.py) pass", tools.check(proj, proj.workdir, other))
        self.assertIn("do not pass yet: 0 of 2 spec examples pass", tools.check(proj, proj.workdir, {"sees": {"read": ["**"], "write": ["**"]}}))

    def test_a_workers_check_covers_command_line_tools_by_their_script(self):
        from codebot.pipeline import tools
        spec = ("# Tools\n\n## Requirements\n\n1. `up.py` prints its input in upper case. `python3 up.py` with standard input `ab` prints `AB`.\n"
                "2. `low.py` prints its input in lower case. `python3 low.py` with standard input `AB` prints `ab`.\n")
        home = _home(); self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        proj = _proj(home, "w12", os.path.join(home, *self.CALC), spec)
        with open(os.path.join(proj.workdir, "up.py"), "w") as f: f.write("import sys\nprint(sys.stdin.read().strip().upper())\n")
        self.assertIn("The examples pass: 1 of 1 spec examples for your files (up.py) pass", tools.check(proj, proj.workdir, {"sees": {"read": ["**"], "write": ["up.py"]}}))
        self.assertIn("do not pass yet: 0 of 1 spec examples for your files (low.py) pass", tools.check(proj, proj.workdir, {"sees": {"read": ["**"], "write": ["low.py"]}}))

    def test_a_failed_gate_tells_the_agent_what_failed(self):
        proj, run, events, s = self._run("w7", [BAD, FIN, GOOD, FIN])
        self.assertEqual(run["status"], "COMPLETED", events)
        self.assertEqual(run["gates"]["check"]["fails"], 1)
        again = s.seen[2][2]
        self.assertIn("The harness checked the result (gate `check`) and it FAILED. Fix this:\n0 of 1 spec examples pass", again); self.assertIn("add(2, 3)", again)
        self.assertNotIn("Your turns on this task", again)              # a new visit starts clean
        self.assertNotIn("feedback", run)

NONE = [{"action": "findings", "findings": []}]
PLAN = [{"action": "write", "path": "PLAN.md", "content": "mathx.py: add(a, b)\nui.py: show(x)\n"}]          # the plan step ends when PLAN.md is written
SOME = [{"action": "findings", "findings": [{"where": "ui.py", "what": "look at show again"}]}]
UI = {"action": "write", "path": "ui.py", "content": "def show(x): return str(x)\n"}

def _design(case, reply, spec=TWO, **env):
    """(home, bundle, meta) of the Team Designer's answer to a scripted proposal; env = project settings (NUM_CTX, PIPELINE_SHAPE)."""
    home = _home(); case.addCleanup(shutil.rmtree, home, ignore_errors=True)
    class P:
        def get(self, k, d=None, cast=str): return cast(env[k]) if k in env else d
    chat = lambda proj, messages, **kw: {"content": reply if isinstance(reply, str) else json.dumps(reply)}
    bundle, meta = design.propose(home, spec, chat_fn=chat, proj=P() if env else None)
    return home, bundle, meta

def _project(home, name, bundle, meta, spec=TWO, git=False):
    import subprocess
    pd = os.path.join(home, "projects", name); os.makedirs(pd); code = os.path.join(home, "code", name); os.makedirs(code)
    with open(os.path.join(code, "SPEC.md"), "w") as f: f.write(spec)
    with open(os.path.join(pd, "project.env"), "w") as f: f.write(f'WORKDIR="{code}"\nRUNDIR="{code}"\n')
    if git:             # as `codebot start` leaves a new folder: a repository on the project's own branch
        subprocess.run(["git", "-C", code, "init", "-q"], check=True); subprocess.run(["git", "-C", code, "checkout", "-q", "-b", f"codebot/{name}"], check=True)
        open(os.path.join(pd, ".git-prepped"), "w").close()
    design.write_project_pipeline(Project(name, home), bundle, template_meta=meta)
    return Project(name, home)

def W(i, files, lines=40, **kw): return {"id": i, "responsibility": f"write {files}", "files": files, "lines": lines, **kw}
PAIR = {"summary": "two files", "workers": [W("ui", ["ui.py"], needs=["math"]), W("math", ["mathx.py"])]}
BIG = {"summary": "two big files", "workers": [W("ui", ["ui.py"], 900, needs=["math"]), W("math", ["mathx.py"], 700)]}

class TestTeamDesigner(unittest.TestCase):
    """The team is sized by whether the program fits one agent's context, and there is always a plan, a build and a review."""
    def test_a_program_that_fits_one_context_is_plan_build_review_fix(self):
        home, bundle, meta = _design(self, PAIR)
        self.assertEqual([m["id"] for m in bundle["team"]["members"]], ["planner", "builder", "reviewer"])
        self.assertEqual([(n["id"], n.get("action")) for n in bundle["workflow"]["nodes"]],
                         [("start", None), ("plan", "write_plan"), ("build", "drafts"), ("review", "review"), ("fix", "fix"), ("gate", None), ("done", None)])
        self.assertIn({"from": "gate", "to": "fix", "kind": "execution", "when": "fail"}, bundle["workflow"]["edges"])
        self.assertEqual((meta["shape"], meta["fit"]["fits"], meta["fit"]["lines"]), ("plan-build-review-fix", True, 80))
        self.assertEqual((bundle["agents"]["reviewer"]["memory"], bundle["agents"]["reviewer"]["sees"]["write"]), ("none", []))

    def test_the_small_team_runs_and_the_builder_gets_the_reviewers_findings(self):
        home, bundle, meta = _design(self, PAIR); proj = _project(home, "small", bundle, meta)
        found = [{"action": "run", "command": "python3 -c 'import ui; print(repr(ui.show(5)))'"},
                 {"action": "findings", "findings": [{"where": "ui.py", "what": "show(5) returns 5, the SPEC requires '5'", "serious": "high"}]}]
        s = Rec({"planner": PLAN, "builder": [GOOD, {"action": "write", "path": "ui.py", "content": "def show(x): return x\n"}, FIN, UI, {"action": "check"}, FIN],
                 "reviewer": found})
        run, events = schedule.run_until_idle(proj, chat_fn=s, max_steps=120)
        self.assertEqual(run["status"], "COMPLETED", events[-8:])
        self.assertEqual([m for m, *_ in s.seen], ["planner"] + ["builder"] * 3 + ["reviewer"] * 2 + ["builder"] * 3)
        self.assertNotIn("check", bundle["agents"]["reviewer"]["tools"])
        self.assertEqual((bundle["agents"]["reviewer"]["model"], "model" in bundle["agents"]["builder"]), ({"num_predict": 900}, False))
        self.assertEqual([(p["node"], p["passed"]) for p in run["progress"]], [("plan", 0), ("build", 1), ("fix", 2)])
        fix = s.seen[6][2]
        self.assertIn("[FINDING] from reviewer", fix); self.assertIn("show(5) returns 5, the SPEC requires '5'", fix); self.assertIn("PLAN.md", fix)
        self.assertIn("1. run `python3 -c 'import ui; print(repr(ui.show(5)))'`\nexit status 0\n5", s.seen[5][2])     # the reviewer saw it fail

    def test_with_no_findings_the_fix_step_costs_no_model_call(self):
        home, bundle, meta = _design(self, PAIR); proj = _project(home, "clean", bundle, meta)
        s = Rec({"planner": PLAN, "builder": [GOOD, UI, FIN], "reviewer": NONE})
        run, events = schedule.run_until_idle(proj, chat_fn=s, max_steps=120)
        self.assertEqual(run["status"], "COMPLETED", events[-8:])
        self.assertEqual(([m for m, *_ in s.seen], run["skipped"]), (["planner"] + ["builder"] * 3 + ["reviewer"], ["fix"]))

    def test_findings_already_dealt_with_are_not_shown_again(self):
        home, bundle, meta = _design(self, PAIR); proj = _project(home, "again", bundle, meta)
        s = Rec({"planner": PLAN, "builder": [GOOD, FIN, FIN, UI, FIN], "reviewer": [{"action": "findings", "findings": [{"where": "ui.py", "what": "ui.py is missing"}]}]})
        run, events = schedule.run_until_idle(proj, chat_fn=s, max_steps=120)
        self.assertEqual(run["status"], "COMPLETED", events[-8:])                    # fix finished without ui.py, the gate failed, fix again
        visits = [u for m, _, u, _ in s.seen if m == "builder"]
        self.assertIn("ui.py is missing", visits[2]); self.assertNotIn("ui.py is missing", visits[3])
        self.assertIn("The harness checked the result (gate `gate`) and it FAILED", visits[3])

    def test_a_program_too_big_for_one_context_gets_a_manager_teams_and_qa(self):
        home, bundle, meta = _design(self, BIG, NUM_CTX=16384)
        self.assertEqual((meta["shape"], meta["fit"]["fits"], meta["fit"]["lines"], meta["fit"]["budget"]), ("manager-teams-qa", False, 1600, 6000))
        self.assertEqual([m["id"] for m in bundle["team"]["members"]], ["planner", "manager", "math", "math-review", "ui", "ui-review", "qa"])
        self.assertEqual([n["id"] for n in bundle["workflow"]["nodes"]],
                         ["start", "plan", "contracts", "build-math", "review-math", "fix-math", "build-ui", "review-ui", "fix-ui", "integrate", "refix-math", "refix-ui",
                          "manager-review", "qa-write", "qa-gate", "qa-fix", "gate", "done"])
        self.assertEqual(bundle["team"]["pods"][0], {"id": "team-math", "manager": "manager", "members": ["math", "math-review"]})
        self.assertEqual(bundle["agents"]["w-math"]["sees"]["write"], ["mathx.py"]); self.assertEqual(bundle["project"]["reserved"], {"qa_tests.py": "qa"})
        validate_project(bundle)

    def test_the_context_window_decides_not_the_number_of_files(self):
        mid = {"summary": "", "workers": [W("ui", ["ui.py"], 300), W("math", ["mathx.py"], 300)]}                 # 600 lines: 6,600 tokens
        self.assertEqual(_design(self, mid, NUM_CTX=16384)[2]["shape"], "manager-teams-qa")
        self.assertEqual(_design(self, mid, NUM_CTX=65536, DRAFTS_NUM_PREDICT=12000)[2]["shape"], "plan-build-review-fix")
        self.assertEqual(_design(self, BIG, PIPELINE_SHAPE="small")[2]["shape"], "plan-build-review-fix")
        self.assertEqual(_design(self, PAIR, PIPELINE_SHAPE="large")[2]["shape"], "manager-teams-qa")
        one = _design(self, {"summary": "", "workers": [W("all", ["mathx.py", "ui.py"], 5000)]}, NUM_CTX=16384)[2]
        self.assertEqual(one["shape"], "plan-build-review-fix"); self.assertIn("was not split into two or more parts", " ".join(one["notes"]))

    def test_the_large_team_runs_from_plan_to_qa(self):
        home, bundle, meta = _design(self, PAIR, PIPELINE_SHAPE="large"); proj = _project(home, "large", bundle, meta)
        qa_ok = {"action": "write", "path": "qa_tests.py", "content": "import mathx, ui, sys\nprint('add'); sys.exit(0 if mathx.add(2, 2) == 4 and ui.show(7) == '7' else 1)\n"}
        s = Rec({"planner": PLAN,
                 "manager": [{"action": "write", "path": "INTERFACES.md", "content": "mathx.add(a, b) -> number\nui.show(x) -> str\n"},                     # contracts
                             {"action": "check"}, {"action": "findings", "findings": [{"where": "ui.py", "what": "show must return a str"}]},                    # integrate
                             {"action": "write", "path": "qa_tests.py", "content": "exit(0)"}, FIN],                                                           # manager-review
                 "math": [GOOD, FIN, FIN], "math-review": NONE,
                 "ui": [{"action": "write", "path": "mathx.py", "content": "mine"}, {"action": "write", "path": "ui.py", "content": "def show(x): return x\n"}, FIN, UI, FIN],
                 "ui-review": NONE, "qa": [qa_ok, FIN]})
        run, events = schedule.run_until_idle(proj, chat_fn=s, max_steps=200)
        self.assertEqual(run["status"], "COMPLETED", events[-10:])
        who = [m for m, *_ in s.seen]
        self.assertEqual([w for i, w in enumerate(who) if i == 0 or who[i - 1] != w],
                         ["planner", "manager", "math", "math-review", "ui", "ui-review", "manager", "math", "ui", "manager", "qa"])
        self.assertEqual(run["skipped"], ["fix-math", "fix-ui"])                                        # their own reviewers found nothing
        ui_turns = [u for m, _, u, _ in s.seen if m == "ui"]
        self.assertIn("error: write denied: mathx.py", ui_turns[1])                                     # a team writes only its own files
        self.assertIn("show must return a str", ui_turns[3])                                            # the manager's finding reached the team at refix
        self.assertIn("error: qa_tests.py belongs to qa; only qa may write it", [u for m, _, u, _ in s.seen if m == "manager"][4])
        self.assertEqual((run["gates"]["qa-gate"]["result"], run["gates"]["gate"]["result"]), ("pass", "pass"))

    def test_failing_qa_tests_go_to_the_manager_and_a_wrong_test_cannot_block_the_build(self):
        home, bundle, meta = _design(self, PAIR, PIPELINE_SHAPE="large"); proj = _project(home, "qa", bundle, meta)
        wrong = {"action": "write", "path": "qa_tests.py", "content": "import mathx, sys\nprint('add(2, 2) expected 5, got', mathx.add(2, 2)); sys.exit(1)\n"}
        s = Rec({"planner": PLAN, "manager": [FIN, {"action": "findings", "findings": []}, FIN, FIN, FIN, FIN], "math": [GOOD, FIN, FIN, FIN], "math-review": NONE,
                 "ui": [UI, FIN, FIN, FIN], "ui-review": NONE, "qa": [wrong, FIN]})
        run, events = schedule.run_until_idle(proj, chat_fn=s, max_steps=200)
        self.assertEqual(run["status"], "COMPLETED", events[-10:])                   # the spec's examples pass: QA's wrong test does not fail the run
        self.assertEqual(run["gates"]["qa-gate"]["fails"], 3)
        self.assertIn("gate qa-gate still failed after 3 tries and was passed over: QA's tests failed", run["warnings"][0])
        told = [u for m, _, u, _ in s.seen if m == "manager"][3]
        self.assertIn("gate `qa-gate`", told); self.assertIn("add(2, 2) expected 5, got 4", told)

    def test_a_messy_proposal_is_repaired_not_refused(self):
        messy = {"summary": "s", "workers": [
            {"id": "Math Dev!", "responsibility": "math", "files": ["`mathx.py`", "../etc/passwd", "SPEC.md", "qa_tests.py"], "lines": "many", "needs": ["ghost"]},
            {"id": "manager", "responsibility": "ui", "files": ["mathx.py", "./ui.py"], "lines": 20},
            {"id": "idle", "responsibility": "nothing", "files": [], "lines": 5}, "junk"]}
        home, bundle, meta = _design(self, messy, PIPELINE_SHAPE="large")
        self.assertEqual([m["id"] for m in bundle["team"]["members"]], ["planner", "manager", "math-dev", "math-dev-review", "manager-2", "manager-2-review", "qa"])
        self.assertEqual(bundle["agents"]["w-math-dev"]["sees"]["write"], ["mathx.py"]); self.assertEqual(bundle["agents"]["w-manager-2"]["sees"]["write"], ["ui.py"])
        notes = " | ".join(meta["notes"])
        for part in ("'../etc/passwd'", "'SPEC.md'", "'qa_tests.py'", "mathx.py already belongs to math-dev", "idle: dropped"): self.assertIn(part, notes)

    def test_nothing_usable_is_still_plan_build_review_fix(self):
        for reply in ("not json at all", {"workers": "many"}, {}):
            home, bundle, meta = _design(self, reply)
            self.assertEqual((meta["shape"], [m["id"] for m in bundle["team"]["members"]]), ("plan-build-review-fix", ["planner", "builder", "reviewer"]))

    def test_a_spec_without_examples_ends_in_an_approval_not_a_gate_that_cannot_pass(self):
        home, bundle, meta = _design(self, PAIR, spec="# Two\n\n1. `mathx.py` adds.\n2. `ui.py` shows.\n")
        self.assertEqual([n["type"] for n in bundle["workflow"]["nodes"]][-2:], ["approve", "end"])
        self.assertIn("nothing checks the result but your approval", " ".join(meta["notes"]))

    def test_the_designer_is_asked_for_parts_and_their_size_not_for_a_team(self):
        home = _home(); self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        got = {}
        def chat(proj, messages, **kw): got.update(system=messages[0]["content"], user=messages[-1]["content"], schema=kw["schema"]); return {"content": json.dumps(PAIR)}
        design.propose(home, TWO, chat_fn=chat)
        self.assertIn("Source files the SPEC names: mathx.py, ui.py.", got["user"]); self.assertIn("the harness sizes it from your estimate", got["system"])
        self.assertIn("lines", got["schema"]["properties"]["workers"]["items"]["required"])

FINDING = [{"action": "findings", "findings": [{"where": "SPEC", "what": "ok", "serious": "low"}]}]
CALC = ("pipelines", "templates", "projects", "basic-calculator.json")
PODS = ("pipelines", "templates", "projects", "small-pods.json")

class TestTheSpecIsFrozen(unittest.TestCase):
    """No agent can change what its work is judged by."""
    def _run(self, name, plan, tpl=CALC, check=None, files=None):
        home = _home(); self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        proj = _proj(home, name, os.path.join(home, *tpl), SPEC, check=check)
        for n, text in (files or {}).items():
            with open(os.path.join(proj.workdir, n), "w") as f: f.write(text)
        s = Rec(plan); run, events = schedule.run_until_idle(proj, chat_fn=s, max_steps=120)
        return proj, run, events, s

    def _spec(self, proj):
        with open(os.path.join(proj.workdir, "SPEC.md")) as f: return f.read()

    def test_an_agent_cannot_pass_by_rewriting_the_specs_example(self):
        proj, run, events, s = self._run("f1", {"dev": [BAD, {"action": "write", "path": "./SPEC.md", "content": SPEC.replace("is `5`", "is `-1`")}, FIN]})
        self.assertEqual(run["status"], "FAILED", events)                       # before the freeze this run ended COMPLETED
        self.assertIn("error: SPEC.md is frozen: it is what the result is judged by", s.seen[2][2])
        self.assertEqual(self._spec(proj), SPEC)
        self.assertIn("0 of 1 spec examples pass", run["error"])

    def test_a_spec_changed_by_a_command_is_put_back_before_anything_reads_it(self):
        proj, run, events, s = self._run("f2", {"dev": [BAD, {"action": "run", "command": "sed -i 's/is `5`/is `-1`/' SPEC.md"}, {"action": "check"}, FIN]})
        self.assertEqual(run["status"], "FAILED", events)
        self.assertIn("3. check\nThe examples do not pass yet: 0 of 1", s.seen[3][2])
        self.assertEqual(self._spec(proj), SPEC)
        self.assertIn("SPEC.md had been changed in the code directory after the spec was frozen and was put back", run["warnings"])

    def test_a_verify_sh_written_during_the_build_is_never_run(self):
        easy = {"action": "write", "path": "verify.sh", "content": "exit 0\n"}
        proj, run, events, s = self._run("f3", {"sync": [FIN], "math-dev": [easy, FIN], "ui-dev": [FIN]}, tpl=PODS, check="tests")
        self.assertEqual(run["status"], "FAILED", events[-6:])
        self.assertIn("there was no verify.sh when the specification was frozen", run["error"])
        proj, run, events, s = self._run("f4", {"sync": [FIN], "math-dev": [easy, GOOD, FIN], "ui-dev": [FIN]}, tpl=PODS, check="tests",
                                         files={"verify.sh": "python3 -c 'import mathx; assert mathx.add(2, 3) == 5'\n"})
        self.assertEqual(run["status"], "COMPLETED", events[-6:])
        self.assertIn("error: verify.sh is frozen", s.seen[2][2])

    def test_spec_work_may_still_write_the_spec_and_its_gate_freezes_it(self):
        from codebot.pipeline import frozen
        better = SPEC + "2. Also `add(0, 0)` is `0`.\n"
        proj, run, events, s = self._run("f5", {"elephant": [{"action": "write", "path": "SPEC.md", "content": better}, FIN, FIN], "gf-security": FINDING,
                                                "gf-structure": FINDING, "gf-ui": FINDING}, tpl=("pipelines", "templates", "projects", "spec-with-reviewers.json"))
        self.assertEqual(run["status"], "COMPLETED", events[-6:])
        self.assertEqual((frozen.info(proj)["version"], frozen.text(proj, proj.workdir)), (1, better))
        self.assertIn("frozen as version 1", run["gates"]["gate"]["detail"])
        self.assertTrue(frozen.protected(proj, "SPEC.md")); self.assertFalse(frozen.protected(proj, "mathx.py"))

class TestCheckpoints(unittest.TestCase):
    """A member's finished work is committed, and work that breaks examples that passed is sent back once, then set aside."""
    ZERO = {"action": "write", "path": "mathx.py", "content": "def add(a, b): return 0\n"}

    def _team(self, name, fix, git=True):
        import subprocess
        home, bundle, meta = _design(self, PAIR); proj = _project(home, name, bundle, meta, git=git)
        s = Rec({"planner": PLAN, "builder": [GOOD, UI, FIN] + fix, "reviewer": SOME})
        run, events = schedule.run_until_idle(proj, chat_fn=s, max_steps=120)
        log = subprocess.run(["git", "-C", proj.workdir, "log", "--format=%s", "--reverse"], capture_output=True, text=True).stdout.splitlines() if git else []
        with open(os.path.join(proj.workdir, "mathx.py")) as f: return proj, run, events, s, log, f.read()

    def test_each_members_finished_work_is_a_commit(self):
        proj, run, events, s, log, math = self._team("c1", [{"action": "check"}, FIN])
        self.assertEqual(run["status"], "COMPLETED", events[-6:])
        self.assertEqual(log, ["pipeline: planner finished plan (0 of 2 spec examples pass)", "pipeline: builder finished build (2 of 2 spec examples pass)"])
        self.assertEqual([(p["node"], p["passed"]) for p in run["progress"]], [("plan", 0), ("build", 2), ("fix", 2)])
        self.assertNotIn("warnings", run)

    def test_work_that_breaks_a_passing_example_goes_back_once_and_can_be_fixed(self):
        proj, run, events, s, log, math = self._team("c2", [BAD, FIN, GOOD, FIN])
        self.assertEqual(run["status"], "COMPLETED", events[-6:])
        self.assertEqual((run["regressions"], run["gates"]["gate"]["fails"]), ({"fix": 1}, 0))
        again = [u for m, _, u, _ in s.seen if m == "builder"][5]
        self.assertIn("Your last changes broke examples that passed before. Fix this:\n1 of the spec's examples that passed before builder's work on `fix` fail now", again)
        self.assertIn("add(2, 3)", again); self.assertNotIn("warnings", run); self.assertNotIn("feedback", run)

    def test_work_that_breaks_them_twice_is_set_aside_and_the_code_goes_back(self):
        proj, run, events, s, log, math = self._team("c3", [BAD, FIN, self.ZERO, FIN])
        self.assertEqual(run["status"], "COMPLETED", events[-6:])                          # the gate judges the checkpoint, which passes
        self.assertEqual(math, GOOD["content"])
        self.assertEqual(run["warnings"], ["builder's work on fix broke passing examples twice and was set aside (artifact set-aside-fix-v1); "
                                           "the code is back at the last checkpoint"])
        from codebot.pipeline import artifacts
        patch = artifacts.read(R.pipeline_dir(proj), [a for a in artifacts.list_artifacts(R.pipeline_dir(proj)) if a["kind"] == "patch"][0])
        self.assertIn("+def add(a, b): return 0", patch)
        self.assertEqual(len(log), 2)

    def test_without_a_branch_of_its_own_nothing_is_committed_or_reset(self):
        proj, run, events, s, log, math = self._team("c4", [BAD, FIN, self.ZERO, FIN], git=False)
        self.assertEqual(run["status"], "FAILED", events[-6:])                             # the broken work stayed, so the gate fails
        self.assertEqual(run["warnings"][0], "builder's work on fix was kept although it broke passing examples twice: no rollback, because the code directory is not a git repository")
        self.assertFalse(os.path.isdir(os.path.join(proj.workdir, ".git")))

class TestCrashRecovery(unittest.TestCase):
    def test_the_member_that_was_in_a_call_takes_that_turn_again(self):
        home = _home(); self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        proj = _proj(home, "cr", os.path.join(home, "pipelines", "templates", "projects", "spec-debate.json"), SPEC)
        s = Rec({"elephant": [FIN, FIN], "gf-security": [{"action": "ask", "to": "gf-structure", "text": "is auth covered?"}] + FINDING,
                 "gf-structure": [{"action": "answer", "text": "yes"}] + FINDING, "gf-ui": FINDING})
        state = {"crashed": False}
        def chat(p, messages, **kw):
            if "You are member `gf-structure`" in messages[0]["content"] and not state["crashed"]:
                state["crashed"] = True; raise KeyboardInterrupt("the process dies in the middle of the call")
            return s(p, messages, **kw)
        with self.assertRaises(KeyboardInterrupt): schedule.run_until_idle(proj, chat_fn=chat, max_steps=60)
        left = R.load_run(proj)["members"]
        self.assertEqual((left["gf-security"]["state"], left["gf-structure"]["state"]), ("WAITING", "RUNNING"))
        run, events = schedule.run_until_idle(Project("cr", home), chat_fn=chat, max_steps=80)
        self.assertEqual(run["status"], "COMPLETED", events[-8:])
        self.assertEqual(run["recovered"], 1)
        self.assertEqual(s.calls[:4], ["elephant", "gf-security", "gf-structure", "gf-security"])        # the answer, then the asker: no second question
        self.assertEqual(len([m for m in mailbox.iter_messages(R.pipeline_dir(proj)) if m["type"] == "QUESTION"]), 1)

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
