"""What the pipeline guarantees when things go wrong: a person's approval, a crash at any point of a turn, work that breaks what passed, a
budget that runs out, a path that is not what it seems. Fake model throughout; crashes are injected at the scheduler's persistence boundaries."""
import json, os, shutil, subprocess, tempfile, time, unittest
import test_pipeline_runtime as T
from test_pipeline_runtime import FIN, GOOD, BAD, SPEC, Rec, _home
from codebot.config import Project
from codebot.pipeline import artifacts, design, journal, mailbox, runtime as R, schedule, scope, teamgen, tools
from codebot.pipeline.validate import ValidationError

V = "codebot.pipeline/v1"
def agent(aid, **kw):
    return {"schema": V, "kind": "agent", "id": aid, "name": aid, "goal": aid, "instructions": aid, "memory": "task", "sees": {"read": ["**"], "write": ["**"]},
            "tools": ["read", "list", "check", "run"], "can_talk_to": [], "messages": ["TASK", "QUESTION", "ANSWER", "FINDING", "RESULT"], **kw}

def mini(case, name, nodes, edges, members=None, agents=None, env="", spec=SPEC, git=False):
    """A project with the given workflow; members default to one writer `a`."""
    home = _home(); case.addCleanup(shutil.rmtree, home, ignore_errors=True)
    members = members or [{"id": "a", "agent": "w", "can_talk_to": []}]
    bundle = {"project": {"schema": V, "kind": "project", "id": name, "name": name}, "agents": agents or {"w": agent("w")},
              "team": {"schema": V, "kind": "team", "id": "t", "name": "t", "members": members},
              "workflow": {"schema": V, "kind": "workflow", "id": "wf", "name": "wf", "nodes": nodes, "edges": edges}}
    pd = os.path.join(home, "projects", name); os.makedirs(pd); code = os.path.join(home, "code", name); os.makedirs(code)
    with open(os.path.join(code, "SPEC.md"), "w") as f: f.write(spec)
    with open(os.path.join(pd, "project.env"), "w") as f: f.write(f'WORKDIR="{code}"\nRUNDIR="{code}"\n{env}')
    if git:
        subprocess.run(["git", "-C", code, "init", "-q"], check=True); subprocess.run(["git", "-C", code, "checkout", "-q", "-b", f"codebot/{name}"], check=True)
        open(os.path.join(pd, ".git-prepped"), "w").close()
    design.write_project_pipeline(Project(name, home), bundle)
    return Project(name, home)

def N(i, typ, **kw): return {"id": i, "type": typ, **kw}
def E(a, b, **kw): return {"from": a, "to": b, **kw}
ONE = ([N("start", "start"), N("work", "run", agent="a"), N("ok", "approve"), N("done", "end")], [E("start", "work"), E("work", "ok"), E("ok", "done")])
def go(proj, plan, steps=80):
    s = plan if isinstance(plan, Rec) else Rec(plan); run, events = schedule.run_until_idle(Project(proj.name, proj.home), chat_fn=s, max_steps=steps)
    return run, events, s

class TestApproval(unittest.TestCase):
    def test_without_a_setting_an_approval_waits_for_a_person(self):
        proj = mini(self, "ap1", *ONE); run, events, s = go(proj, {"a": [FIN]})
        self.assertEqual((run["status"], run["approve_node"]), ("WAITING_APPROVAL", "ok")); self.assertIn("codebot pipeline approve ap1", events[-1]["message"])
        again, _, _ = go(proj, s); self.assertEqual((again["status"], again["step_count"]), ("WAITING_APPROVAL", run["step_count"]))       # waiting costs nothing
        schedule.approve(Project("ap1", proj.home)); done, _, _ = go(proj, s)
        self.assertEqual((done["status"], done["result"]), ("COMPLETED", "approved_unverified"))        # a person said yes; nothing was checked mechanically
        self.assertEqual(done["approvals"]["ok#0"]["decision"], "approved")

    def test_automatic_approval_has_to_be_asked_for_and_verifies_nothing(self):
        proj = mini(self, "ap2", *ONE, env='PIPELINE_AUTO_APPROVE="1"\n'); run, events, s = go(proj, {"a": [FIN]})
        self.assertEqual((run["status"], run["result"]), ("COMPLETED", "unverified")); self.assertTrue(run["approvals"]["ok#0"]["by"].startswith("auto"))

    def test_a_rejection_ends_the_run_or_follows_the_fail_edge(self):
        proj = mini(self, "ap3", *ONE); run, _, s = go(proj, {"a": [FIN]})                          # the only way on is unlabelled: not a way for a rejection
        schedule.reject(Project("ap3", proj.home), reason="the plan misses the import step"); run = R.load_run(proj)
        self.assertEqual((run["status"], run["result"], run["error"]), ("FAILED", "rejected", "rejected at approval ok: the plan misses the import step"))
        proj = mini(self, "ap3b", ONE[0], [E("start", "work"), E("work", "ok"), E("ok", "done", when="pass"), E("ok", "work", when="fail")])
        run, _, s = go(proj, {"a": [FIN, GOOD, FIN]}); schedule.reject(Project("ap3b", proj.home), reason="no")
        run, _, _ = go(proj, s); self.assertEqual((run["status"], run["approve_node"], run["approve_seq"]), ("WAITING_APPROVAL", "ok", {"ok": 1}))   # back through work, and asked again

    def test_an_approval_with_nothing_waiting_changes_nothing_and_one_decision_is_one_decision(self):
        proj = mini(self, "ap4", *ONE); p = Project("ap4", proj.home)
        self.assertEqual(schedule.approve(p).get("status"), "idle")
        schedule.step(p, chat_fn=Rec({"a": [FIN]}))
        self.assertEqual(schedule.approve(p)["error"], "nothing is waiting for approval"); self.assertNotIn("approvals", R.load_run(p))
        run, _, s = go(proj, {"a": [FIN]}); schedule.approve(p); go(proj, s); self.assertEqual(R.load_run(p)["status"], "COMPLETED")
        self.assertEqual(schedule.approve(p)["error"], "nothing is waiting for approval"); self.assertEqual(list(R.load_run(p)["approvals"]), ["ok#0"])

    def test_approving_one_node_does_not_approve_the_next(self):
        nodes = [N("start", "start"), N("work", "run", agent="a"), N("first", "approve"), N("second", "approve"), N("done", "end")]
        proj = mini(self, "ap5", nodes, [E("start", "work"), E("work", "first"), E("first", "second"), E("second", "done")]); run, _, s = go(proj, {"a": [FIN]})
        schedule.approve(Project("ap5", proj.home)); run, _, _ = go(proj, s)
        self.assertEqual((run["status"], run["approve_node"], sorted(run["approvals"])), ("WAITING_APPROVAL", "second", ["first#0"]))
        schedule.approve(Project("ap5", proj.home)); go(proj, s); self.assertEqual(R.load_run(proj)["status"], "COMPLETED")

    def test_a_waiting_approval_survives_a_restart(self):
        proj = mini(self, "ap6", *ONE); go(proj, {"a": [FIN]})
        with open(R.run_path(proj)) as f: saved = json.load(f)
        self.assertEqual((saved["status"], saved["approve_node"]), ("WAITING_APPROVAL", "ok"))
        fresh = Project("ap6", proj.home)                         # a new process: nothing in memory
        self.assertEqual(schedule.step(fresh)["status"], "WAITING_APPROVAL"); schedule.approve(fresh); schedule.run_until_idle(fresh)
        self.assertEqual((R.load_run(fresh)["status"], R.load_run(fresh)["approvals"]["ok#0"]["by"]), ("COMPLETED", "user"))

    def test_an_examples_gate_that_passes_is_what_makes_a_run_verified(self):
        nodes = [N("start", "start"), N("work", "run", agent="a"), N("gate", "gate", check="examples"), N("done", "end")]
        proj = mini(self, "ap7", nodes, [E("start", "work"), E("work", "gate"), E("gate", "done", when="pass")]); run, _, _ = go(proj, {"a": [GOOD, FIN]})
        self.assertEqual((run["status"], run["result"]), ("COMPLETED", "verified"))

class TestMailbox(unittest.TestCase):
    def setUp(self): self.d = _home(); self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
    def raw(self): return open(mailbox.path(self.d), "rb").read()

    def test_marking_read_appends_an_event_and_never_changes_a_message(self):
        q = mailbox.send(self.d, "a", "b", "QUESTION", {"text": "?"}, blocking=True); f = mailbox.send(self.d, "c", "b", "FINDING", {"findings": [1]}); before = self.raw()
        self.assertEqual(mailbox.mark_read(self.d, [f["id"]]), [f["id"]])
        self.assertTrue(self.raw().startswith(before)); self.assertEqual(json.loads(self.raw().splitlines()[-1])["event"], "read")
        self.assertEqual([(m["id"], m["state"]) for m in mailbox.inbox(self.d, "b")], [(q["id"], "delivered"), (f["id"], "read")])
        self.assertEqual([m["id"] for m in mailbox.inbox(self.d, "b", unread_only=True)], [q["id"]])

    def test_a_second_acknowledgment_writes_nothing(self):
        m = mailbox.send(self.d, "a", "b", "RESULT", {}); mailbox.mark_read(self.d, [m["id"]]); size = len(self.raw())
        self.assertEqual(mailbox.mark_read(self.d, [m["id"], m["id"], "no-such-id"]), []); self.assertEqual(len(self.raw()), size)

    def test_answers_keep_their_question_and_thread_after_read_events(self):
        q = mailbox.send(self.d, "a", "b", "QUESTION", {"text": "?"}, blocking=True); self.assertEqual([m["id"] for m in mailbox.pending_questions(self.d)], [q["id"]])
        ans = mailbox.answer(self.d, "b", q, {"text": "!"}); mailbox.mark_read(self.d, [q["id"]])
        self.assertEqual(mailbox.pending_questions(self.d), []); self.assertEqual((ans["reply_to"], ans["thread"], ans["correlation"]), (q["id"], q["thread"], q["correlation"]))
        self.assertEqual([m["type"] for m in mailbox.iter_messages(self.d)], ["QUESTION", "ANSWER"])                      # events are not messages

    def test_a_log_from_before_read_events_is_still_read_correctly(self):
        with open(mailbox.path(self.d), "w") as f:
            f.write(json.dumps({"id": "old1", "from": "a", "to": "b", "type": "FINDING", "payload": {}, "state": "read"}) + "\n")
            f.write(json.dumps({"id": "old2", "from": "a", "to": "b", "type": "FINDING", "payload": {}, "state": "delivered"}) + "\n")
        self.assertEqual([(m["id"], m["state"]) for m in mailbox.inbox(self.d, "b")], [("old1", "read"), ("old2", "delivered")])
        self.assertEqual(mailbox.mark_read(self.d, ["old1", "old2"]), ["old2"])

    def test_a_line_cut_off_by_a_crash_spoils_only_itself(self):
        m = mailbox.send(self.d, "a", "b", "FINDING", {})
        with open(mailbox.path(self.d), "a") as f: f.write('{"id": "torn", "from": "a", "to')            # the process died mid-write
        n = mailbox.send(self.d, "a", "b", "RESULT", {}); mailbox.mark_read(self.d, [m["id"]])
        self.assertEqual([(x["id"], x["state"]) for x in mailbox.inbox(self.d, "b")], [(m["id"], "read"), (n["id"], "delivered")])

    def test_the_same_turn_does_not_send_or_file_twice(self):
        a = mailbox.send(self.d, "a", "b", "QUESTION", {"text": "?"}, inv="inv-1"); b = mailbox.send(self.d, "a", "b", "QUESTION", {"text": "?"}, inv="inv-1")
        self.assertEqual(a["id"], b["id"]); self.assertEqual(len(list(mailbox.iter_messages(self.d))), 1)
        x = artifacts.put(self.d, "notes", "one", producer="a", inv="inv-1"); y = artifacts.put(self.d, "notes", "one", producer="a", inv="inv-1")
        self.assertEqual((x["id"], y["id"], len(artifacts.list_artifacts(self.d))), ("notes-v1", "notes-v1", 1))
        self.assertEqual(artifacts.put(self.d, "notes", "two", producer="a", inv="inv-2")["id"], "notes-v2")

TWO_MEMBERS = [{"id": "a", "agent": "w", "can_talk_to": ["b"]}, {"id": "b", "agent": "w", "can_talk_to": ["a"]}]
WORK = ([N("start", "start"), N("work", "run", agent="a"), N("done", "end")], [E("start", "work"), E("work", "done")])

class TestACrashInsideATurn(unittest.TestCase):
    """The process is stopped at each persistence boundary of the turn that carries `target`; after the restart the turn's effects exist once."""
    POINTS = ("prepared", "decided", "effects", "applied")
    def crash_then_finish(self, name, point, plan, target, members=None):
        proj = mini(self, name, *WORK, members=members); s = Rec(plan); state = {"armed": False, "done": False}
        def chat(p, messages, **kw):
            out = s(p, messages, **kw); state["armed"] = not state["done"] and json.loads(out["content"]).get("action") == target; return out
        def fault(at):
            if at == "prepared" and not state["done"] and point == "prepared" and s.plan["a"] and s.plan["a"][0].get("action") == target:
                state["done"] = True; raise KeyboardInterrupt(at)
            if state["armed"] and at == point: state["armed"] = False; state["done"] = True; raise KeyboardInterrupt(at)
        schedule.FAULT = fault; self.addCleanup(setattr, schedule, "FAULT", None)
        with self.assertRaises(KeyboardInterrupt): schedule.run_until_idle(proj, chat_fn=chat, max_steps=60)
        schedule.FAULT = None; calls = len(getattr(s, "seen", []))
        run, events = schedule.run_until_idle(Project(name, proj.home), chat_fn=chat, max_steps=60)
        self.assertEqual(run["status"], "COMPLETED", (point, events[-4:]))
        return proj, run, s, calls

    def test_a_write_is_made_once_and_the_model_is_not_asked_twice_for_a_decision_it_gave(self):
        for point in self.POINTS:
            proj, run, s, calls = self.crash_then_finish(f"cw-{point}", point, {"a": [GOOD, FIN]}, "write")
            with open(os.path.join(proj.workdir, "mathx.py")) as f: self.assertEqual(f.read(), GOOD["content"], point)
            rows = tools.turns(R.pipeline_dir(proj), "a"); self.assertEqual([r["action"] for r in rows], ["write", "finish"], point)
            self.assertEqual(len(s.seen), 2, point)                                    # the reply lost with the process is the only one asked for again
            self.assertEqual(run.get("recovered_actions", 0), 0 if point == "prepared" else 1, point)
            states = [e["event"] for e in journal.events(R.pipeline_dir(proj)) if e["inv"] == rows[0]["inv"]]
            self.assertEqual(states[-2:], ["applied", "committed"], point)

    def test_a_question_is_asked_once(self):
        for point in self.POINTS:
            proj, run, s, calls = self.crash_then_finish(f"cq-{point}", point, {"a": [{"action": "ask", "to": "b", "text": "which port?"}, FIN], "b": [{"action": "answer", "text": "8080"}]},
                                                         "ask", members=TWO_MEMBERS)
            msgs = list(mailbox.iter_messages(R.pipeline_dir(proj)))
            self.assertEqual([m["type"] for m in msgs], ["QUESTION", "ANSWER"], point); self.assertEqual(msgs[1]["reply_to"], msgs[0]["id"])
            self.assertEqual([m for m, *_ in s.seen], ["a", "b", "a"], point)

    def test_findings_are_filed_and_sent_once(self):
        found = {"action": "findings", "findings": [{"where": "x", "what": "y"}]}
        for point in self.POINTS:
            proj, run, s, calls = self.crash_then_finish(f"cf-{point}", point, {"a": [found]}, "findings", members=TWO_MEMBERS)
            rd = R.pipeline_dir(proj)
            self.assertEqual([a["id"] for a in artifacts.list_artifacts(rd)], ["findings-a-v1"], point)
            self.assertEqual([m["type"] for m in mailbox.iter_messages(rd)], ["FINDING"], point)

    def test_a_command_that_may_have_run_is_never_run_again(self):
        cmd = {"action": "run", "command": "echo ran >> count.txt"}
        for point, ran in (("prepared", 1), ("decided", 0), ("effects", 1), ("applied", 1)):
            proj, run, s, calls = self.crash_then_finish(f"cr-{point}", point, {"a": [cmd, FIN]}, "run")
            path = os.path.join(proj.workdir, "count.txt"); n = len(open(path).read().split()) if os.path.exists(path) else 0
            self.assertEqual(n, ran, point)                                                      # never 2
            first = tools.turns(R.pipeline_dir(proj), "a")[0]
            if point in ("decided", "effects"):                                                  # the harness cannot know whether it ran: it says so
                self.assertIn("may have been running, and it was not run again", first["result"], point)
                self.assertIn("may or may not have run before a restart", run["warnings"][0], point)
            else: self.assertEqual(first["result"], "exit status 0", point)

class TestRegressionsAndRollback(unittest.TestCase):
    ZERO = {"action": "write", "path": "mathx.py", "content": "def add(a, b): return 0\n"}
    NEG = {"action": "write", "path": "mathx.py", "content": "def add(a, b): return -1\n"}
    FLOW = ([N("start", "start"), N("build", "run", agent="a"), N("fix", "run", agent="a"), N("gate", "gate", check="examples"), N("done", "end")],
            [E("start", "build"), E("build", "fix"), E("fix", "gate"), E("gate", "done", when="pass")])
    def run_it(self, name, fix, env="", git=True, branch=None):
        proj = mini(self, name, *self.FLOW, env=env, git=git)
        if branch: subprocess.run(["git", "-C", proj.workdir, "checkout", "-q", "-b", branch], check=True)
        run, events, s = go(proj, {"a": [GOOD, FIN] + fix}); return proj, run, s
    def git(self, proj, *a): return subprocess.run(["git", "-C", proj.workdir, *a], capture_output=True, text=True).stdout

    def test_the_configured_number_of_retries_is_the_number_of_times_work_goes_back(self):
        for retries, fix, sent_back, calls in ((0, [BAD, FIN], 0, 4), (1, [BAD, FIN, self.ZERO, FIN], 1, 6), (2, [BAD, FIN, self.ZERO, FIN, self.NEG, FIN], 2, 8)):
            proj, run, s = self.run_it(f"rr{retries}", fix, env=f'PIPELINE_REGRESSION_RETRIES="{retries}"\n')
            self.assertEqual((run["status"], run["result"]), ("COMPLETED", "verified"), retries)             # the checkpoint was protected and is what the gate judged
            self.assertEqual((run.get("regressions", {}).get("fix", 0), len(s.seen), len(run["rollbacks"])), (sent_back, calls, 1), retries)
            with open(os.path.join(proj.workdir, "mathx.py")) as f: self.assertEqual(f.read(), GOOD["content"], retries)
            self.assertEqual([o["outcome"] for o in run["outcomes"] if o["node"] == "fix"][-1], "failed", retries)

    def test_a_correction_within_the_retries_is_accepted(self):
        proj, run, s = self.run_it("rok", [BAD, FIN, {"action": "write", "path": "mathx.py", "content": "def add(a, b):\n    return a + b\n"}, FIN])
        self.assertEqual((run["status"], run["regressions"], run.get("rollbacks"), run.get("warnings")), ("COMPLETED", {"fix": 1}, None, None))
        self.assertIn("Your last changes broke examples that passed before", [u for m, _, u, _ in s.seen][4]); self.assertIn("add(2, 3)", s.seen[4][2])
        self.assertEqual([p["passed"] for p in run["progress"]], [1, 1])

    def test_what_is_set_aside_can_be_got_back_untracked_files_included(self):
        note = {"action": "write", "path": "notes.txt", "content": "why I changed add\n"}
        proj, run, s = self.run_it("rb", [BAD, FIN, note, self.ZERO, FIN]); rec = run["rollbacks"][0]
        self.assertEqual((rec["node"], rec["member"], rec["reason"], rec["ref"]), ("fix", "a", "it broke passing examples twice", "refs/codebot/set-aside/fix-1"))
        self.assertTrue(rec["inv"].startswith("inv-")); self.assertFalse(os.path.exists(os.path.join(proj.workdir, "notes.txt")))
        self.assertEqual(self.git(proj, "show", f"{rec['ref']}:mathx.py"), self.ZERO["content"]); self.assertEqual(self.git(proj, "show", f"{rec['ref']}:notes.txt"), note["content"])
        patch = artifacts.read(R.pipeline_dir(proj), [a for a in artifacts.list_artifacts(R.pipeline_dir(proj)) if a["id"] == rec["artifact"]][0])
        self.assertIn("+why I changed add", patch); self.assertIn("+def add(a, b): return 0", patch)

    def test_a_tree_codebot_does_not_own_is_never_committed_to_or_reset(self):
        proj, run, s = self.run_it("ro", [BAD, FIN, self.ZERO, FIN], branch="feature/mine")
        self.assertEqual(run["status"], "FAILED")                                            # the broken work stayed, so the gate fails
        self.assertEqual(self.git(proj, "log", "--oneline", "--all"), ""); self.assertNotIn("rollbacks", run)
        self.assertIn("no rollback, because the code directory is on branch 'feature/mine', not codebot/ro", run["warnings"][0])
        with open(os.path.join(proj.workdir, "mathx.py")) as f: self.assertEqual(f.read(), self.ZERO["content"])
        os.remove(proj.p(".git-prepped")); self.assertEqual(schedule.owned_tree(proj, proj.workdir)[0], False)

class TestOutcomesAndBudgets(unittest.TestCase):
    def test_a_manager_is_told_whether_a_member_finished_or_ran_out(self):
        home, bundle, meta = T._design(self, T.PAIR, PIPELINE_SHAPE="large"); proj = T._project(home, "oc", bundle, meta)
        loop = [{"action": "run", "command": f"echo {i}"} for i in range(40)]
        run, events = schedule.run_until_idle(proj, chat_fn=Rec({"planner": T.PLAN, "manager": [FIN] * 9, "math": loop, "math-review": T.NONE, "ui": [T.UI, FIN], "ui-review": T.NONE,
                                                                 "qa": [FIN]}), max_steps=260)
        results = {m["from"]: m["payload"]["outcome"] for m in mailbox.iter_messages(R.pipeline_dir(proj)) if m["type"] == "RESULT" and m["to"] == "manager"}
        self.assertEqual((results["math"], results["ui"]), ("exhausted_turns", "completed"))
        self.assertIn({"node": "build-math", "member": "math", "visit": 1, "outcome": "exhausted_turns"}, [{k: o[k] for k in ("node", "member", "visit", "outcome")} for o in run["outcomes"]])
        self.assertNotEqual(run.get("result"), "verified")                                 # math wrote nothing: the examples gate cannot have passed

    def test_a_visit_ends_when_its_minutes_are_used(self):
        proj = mini(self, "bv", *WORK, agents={"w": agent("w", limits={"max_minutes": 0.001})}); s = Rec({"a": [{"action": "list"}, {"action": "read", "path": "SPEC.md"}, FIN]})
        def slow(p, messages, **kw): time.sleep(0.08); return s(p, messages, **kw)
        run, events = schedule.run_until_idle(proj, chat_fn=slow, max_steps=40)
        self.assertEqual((run["status"], len(s.seen), run["outcomes"][0]["outcome"]), ("COMPLETED", 1, "exhausted_time"))
        self.assertIn("it used its 0.001 minutes without finishing", run["warnings"][0])

    def test_a_run_out_of_time_is_exhausted_and_a_restart_does_not_refund_the_time(self):
        proj = mini(self, "br", *WORK, env='PIPELINE_MINUTES="0.004"\n'); s = Rec({"a": [{"action": "run", "command": f"echo {i}"} for i in range(30)]})
        def slow(p, messages, **kw): time.sleep(0.1); return s(p, messages, **kw)
        schedule.step(proj, chat_fn=slow); schedule.step(proj, chat_fn=slow); used = R.load_run(proj)["used_s"]; self.assertGreater(used, 0.1)
        run, events = schedule.run_until_idle(Project("br", proj.home), chat_fn=slow, max_steps=60)                # a new process carries the time on
        self.assertEqual((run["status"], run["result"]), ("EXHAUSTED", "exhausted_time")); self.assertGreaterEqual(run["used_s"], used)
        self.assertLess(len(s.seen), 6); self.assertEqual(run["outcomes"][-1]["outcome"], "exhausted_time")
        self.assertTrue(schedule.step(Project("br", proj.home), chat_fn=slow)["idle"])                              # it is over: no further call

    def test_a_command_cannot_outlast_the_time_that_is_left(self):
        proj = mini(self, "bc", *WORK); t0 = time.time()
        out = tools.run(proj, proj.workdir, agent("w"), "a", "sleep 20", cap_s=1)
        self.assertLess(time.time() - t0, 8); self.assertIn("timed out after 1s", out)

    def test_token_use_is_recorded_per_member_and_set_against_the_estimate(self):
        home, bundle, meta = T._design(self, T.PAIR); proj = T._project(home, "us", bundle, meta); s = Rec({"planner": T.PLAN, "builder": [GOOD, T.UI, FIN], "reviewer": T.NONE})
        def chat(p, messages, **kw): return {**s(p, messages, **kw), "prompt_tokens": 1000 + 10 * len(s.seen), "gen_tokens": 50, "secs": 2.0}
        run, events = schedule.run_until_idle(proj, chat_fn=chat, max_steps=80)
        self.assertEqual(run["usage"]["builder"], {"calls": 3, "prompt_tokens": 3090, "gen_tokens": 150, "max_prompt_tokens": 1040, "model_s": 6.0})
        self.assertEqual((run["fit"]["lines"], run["fit"]["fits"]), (80, True))
        self.assertEqual(run["fit_actual"], {"lines": 2, "max_prompt_tokens": 1050, "prompt_tokens": 5150, "gen_tokens": 250, "calls": 5})

class TestScope(unittest.TestCase):
    ANY = {"sees": {"read": ["**"], "write": ["**"]}}
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="cb-ws-"); self.out = tempfile.mkdtemp(prefix="cb-out-"); self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True); self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)
        with open(os.path.join(self.out, "secret.txt"), "w") as f: f.write("outside")

    def test_a_link_to_a_file_outside_is_not_read_and_a_write_replaces_the_link_not_its_target(self):
        os.symlink(os.path.join(self.out, "secret.txt"), os.path.join(self.ws, "notes.txt"))
        with self.assertRaises(ValidationError) as e: scope.read_text(self.ws, self.ANY, "notes.txt")
        self.assertIn("symbolic link", str(e.exception))
        scope.write_text(self.ws, self.ANY, "notes.txt", "mine")
        self.assertFalse(os.path.islink(os.path.join(self.ws, "notes.txt"))); self.assertEqual(open(os.path.join(self.out, "secret.txt")).read(), "outside")
        self.assertEqual(scope.read_text(self.ws, self.ANY, "notes.txt"), "mine")

    def test_a_linked_directory_is_not_entered_for_reading_or_writing(self):
        os.symlink(self.out, os.path.join(self.ws, "data"))
        for call in (lambda: scope.read_text(self.ws, self.ANY, "data/secret.txt"), lambda: scope.write_text(self.ws, self.ANY, "data/new.txt", "x")):
            with self.assertRaises(ValidationError) as e: call()
            self.assertIn("symbolic link", str(e.exception))
        self.assertEqual(os.listdir(self.out), ["secret.txt"])

    def test_a_directory_swapped_for_a_link_after_it_was_used_is_caught_at_the_next_use(self):
        scope.write_text(self.ws, self.ANY, "src/a.py", "ok"); shutil.rmtree(os.path.join(self.ws, "src")); os.symlink(self.out, os.path.join(self.ws, "src"))
        with self.assertRaises(ValidationError): scope.write_text(self.ws, self.ANY, "src/a.py", "again")
        self.assertFalse(os.path.exists(os.path.join(self.out, "a.py")))

    def test_dots_absolute_paths_and_links_in_the_listing(self):
        for bad in ("../x", "a/../../x", "/etc/passwd"):
            with self.assertRaises((ValidationError, FileNotFoundError)): scope.read_text(self.ws, self.ANY, bad)
        scope.write_text(self.ws, self.ANY, "real.py", "x"); os.symlink(os.path.join(self.out, "secret.txt"), os.path.join(self.ws, "link.py"))
        self.assertEqual(tools.listing(self.ws, self.ANY), "real.py (1 bytes, yours to write)")
        scope.write_text(self.ws, self.ANY, "deep/er/file.py", "y"); self.assertEqual(scope.read_text(self.ws, self.ANY, "deep/er/file.py"), "y")

class TestTeamSizing(unittest.TestCase):
    def test_a_dependency_cycle_is_reported_not_silently_ordered(self):
        loop = {"summary": "", "workers": [T.W("ui", ["ui.py"], needs=["math"]), T.W("math", ["mathx.py"], needs=["ui"])]}
        parts, notes = teamgen.clean(loop, T.TWO)
        self.assertEqual([p["id"] for p in parts], ["ui", "math"]); self.assertEqual([p["needs"] for p in parts], [[], []])
        self.assertIn("ui, math need each other in a circle", notes[0])
        ok, notes = teamgen.clean(T.PAIR, T.TWO); self.assertEqual(([p["id"] for p in ok], notes), (["math", "ui"], []))

    def test_the_plan_only_shape_is_a_planner_and_a_builder(self):
        home, bundle, meta = T._design(self, T.PAIR, PIPELINE_SHAPE="plan")
        self.assertEqual((meta["shape"], [m["id"] for m in bundle["team"]["members"]], [n["id"] for n in bundle["workflow"]["nodes"]]),
                         ("plan-build", ["planner", "builder"], ["start", "plan", "build", "gate", "done"]))
        # 2026-11-13: forcing "plan" on a program estimated not to fit gave the manager's teams, so the toolbox series' plan arm on 10 and 20
        # tools was a second copy of the full arm. A forced shape is the shape.
        home, bundle, meta = T._design(self, T.BIG, NUM_CTX=16384, PIPELINE_SHAPE="plan")
        self.assertFalse(meta["fit"]["fits"]); self.assertEqual(meta["shape"], "plan-build")

if __name__ == "__main__": unittest.main()
