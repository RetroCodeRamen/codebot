"""Harness-owned sequential scheduler. At most one model invocation per step() call."""
import json, os, time
from codebot import gitops
from . import artifacts, frozen, gates, journal, mailbox, scope, invoke, tools, runtime as R
from .context import member_agent, may_talk
from .validate import ValidationError

class ScheduleError(Exception): pass

TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "EXHAUSTED")
# How a member's visit of a workflow node ended. Only the first is the agent's own word; none of them says the work is right: gates do.
OUTCOMES = ("completed", "checked", "exhausted_turns", "exhausted_refusals", "exhausted_time", "skipped", "failed", "cancelled")
ENDED = {"checked": "its check passed and it had nothing new to look at", "exhausted_turns": "it used all {turns} turns without finishing",
         "exhausted_refusals": "it was stopped after 3 refused turns in a row without finishing", "exhausted_time": "it used its {minutes:g} minutes without finishing"}
FAULT = None          # tests: a callable(point) that may raise, to stop the process at a persistence boundary

def _fault(point):
    if FAULT: FAULT(point)

def _outcome(run, mid, node_id, outcome, inv=None):
    m = run["members"][mid]; m["outcome"] = outcome
    run.setdefault("outcomes", []).append({"node": node_id, "member": mid, "visit": int(m.get("visit") or 0), "outcome": outcome, **({"inv": inv} if inv else {})})

def _set_member(run, mid, **kw):
    m = run["members"].setdefault(mid, {"state": "SUSPENDED", "node": None, "wait_for": None, "steps": 0})
    m.update(kw)

def _deadlock(run):
    waiting = [mid for mid, m in run["members"].items() if m.get("state") == "WAITING"]
    if not waiting: return False
    ready = [mid for mid, m in run["members"].items() if m.get("state") == "READY"]
    if ready or run.get("queue") or run.get("active"): return False
    # everyone waiting and nothing queued
    return True

def _advance(bundle, run, from_id, when="pass"):
    # a failed gate or a rejected approval follows only an edge labelled `when: fail`: an unlabelled edge is the way on for work that passed
    if when == "fail": edges = [e for e in R.edges_from(bundle, from_id) if e.get("when") == "fail"]
    else: edges = R.edges_from(bundle, from_id, when=when) or R.edges_from(bundle, from_id)
    if not edges:
        run["status"] = "COMPLETED"; run["cursor"] = from_id; return None
    # fan_out: enqueue all targets
    node = R.node_map(bundle).get(from_id) or {}
    if node.get("type") == "fan_out" or len(edges) > 1 and node.get("type") in ("fan_out", "start"):
        # if multiple edges without fan_out type, still queue them sequentially
        pass
    if len(edges) > 1:
        run["queue"] = list(run.get("queue") or [])
        for e in edges:
            run["queue"].append({"node": e["to"], "from": from_id})
        # join bookkeeping: find a join reachable? store pending branch ends as the run nodes themselves
        return _pop_queue(bundle, run)
    return edges[0]["to"]

def _pop_queue(bundle, run):
    q = run.get("queue") or []
    if not q: return None
    item = q.pop(0); run["queue"] = q
    return item["node"]

def run_dir(proj): return R.pipeline_dir(proj)

def apply_action(bundle, proj, run, member_id, agent, action, node, inv=None, cap_s=None):
    """Apply a structured action; may put agent WAITING and ready another. Returns event dict. inv = the model turn it comes from: a message
    or artifact that already carries it is not made again (journal.py)."""
    ws = R.workspace_of(proj, bundle)
    rd = run_dir(proj)
    act = action.get("action")
    ev = {"member": member_id, "action": act, "node": node.get("id")}

    if act == "list": ev["result"] = tools.listing(ws, agent); return ev
    if act == "read": ev["result"] = tools.read(ws, rd, agent, action.get("path")); ev["path"] = action.get("path"); return ev
    if act == "check": ev["result"] = tools.check(proj, ws, agent); return ev
    if act == "run": ev["result"] = tools.run(proj, ws, agent, member_id, action.get("command"), cap_s=cap_s); return ev

    if act in ("ask", "escalate"):
        to = action.get("to")
        if act == "escalate" and not to:
            from .context import escalate_target
            to = escalate_target(bundle, member_id)
        if not to or not may_talk(agent, to): raise ScheduleError(f"{member_id} cannot ask {to!r}")
        text = action.get("text") or ("escalation" if act == "escalate" else "")
        msg = mailbox.send(rd, member_id, to, "QUESTION", {"text": text, "escalate": act == "escalate"}, blocking=True, inv=inv)
        _set_member(run, member_id, state="WAITING", wait_for=msg["id"])
        _set_member(run, to, state="READY", node=run["members"].get(to, {}).get("node") or node.get("id"))
        run["active"] = to
        # queue the asker to resume later via answer
        ev["question_id"] = msg["id"]; ev["to"] = to
        ev["result"] = f"asked {to}; the answer will be in your inbox"
        return ev

    if act == "answer":
        qid = action.get("question_id")
        qs = {m["id"]: m for m in mailbox.pending_questions(rd)}
        # also allow answering any inbox question
        if not qid:
            inbox_q = [m for m in mailbox.inbox(rd, member_id) if m.get("type") == "QUESTION"]
            if not inbox_q: raise ScheduleError("answer without question_id and empty inbox")
            q = inbox_q[-1]; qid = q["id"]
        else:
            all_q = {m["id"]: m for m in mailbox.iter_messages(rd) if m.get("type") == "QUESTION"}
            q = qs.get(qid) or all_q.get(qid)
            if not q: raise ScheduleError(f"unknown question {qid}")
        mailbox.answer(rd, member_id, q, {"text": action.get("text") or ""}, inv=inv)
        mailbox.mark_read(rd, [qid])
        asker = q["from"]
        _set_member(run, asker, state="READY", wait_for=None)
        _set_member(run, member_id, state="COMPLETED" if not mailbox.pending_questions(rd) else "READY")
        # prefer resume asker next
        run["active"] = asker
        ev["question_id"] = qid; ev["to"] = asker
        return ev

    if act == "findings":
        findings = action.get("findings") if isinstance(action.get("findings"), list) else []
        if not findings and action.get("text"): findings = [{"what": action["text"]}]
        text = json.dumps(findings, indent=2, ensure_ascii=False)
        meta = artifacts.put(rd, f"findings-{member_id}", text, producer=member_id, kind="findings", inv=inv)
        # notify architect-like talk targets with FINDING
        for to in agent.get("can_talk_to") or []:
            mailbox.send(rd, member_id, to, "FINDING", {"findings": findings, "artifact": meta["id"]}, inv=inv)
        ev["artifact"] = meta["id"]; ev["result"] = f"{len(findings)} findings sent"
        _done_with_inbox(rd, member_id); _outcome(run, member_id, node.get("id"), "completed", inv)
        _set_member(run, member_id, state="COMPLETED")
        run["active"] = None
        return ev

    if act == "write":
        path = action.get("path") or action.get("artifact_name") or "ARTIFACT.md"
        content = action.get("content") or action.get("text") or ""
        owner = ((bundle.get("project") or {}).get("reserved") or {}).get(os.path.normpath(path))
        if owner and owner != member_id: raise ScheduleError(f"{os.path.normpath(path)} belongs to {owner}; only {owner} may write it")
        if frozen.protected(proj, path):
            raise ScheduleError(f"{os.path.normpath(path)} is frozen: it is what the result is judged by, and no agent may change it. Change the code instead")
        if path.startswith("artifacts/") or action.get("artifact_name"):
            meta = artifacts.put(rd, action.get("artifact_name") or path, content, producer=member_id, kind="doc", inv=inv)
            ev["artifact"] = meta["id"]
        else:
            scope.write_text(ws, agent, path, content)
            ev["path"] = path
        ev["result"] = f"wrote {path} ({len(content)} characters)"
        if node.get("done_after_write") and os.path.normpath(path) == node["done_after_write"]:
            # the step was to write this one document: it is written (the first real planner rewrote PLAN.md five times until its turns ran out)
            _done_with_inbox(rd, member_id); _outcome(run, member_id, node.get("id"), "completed", inv)
            _set_member(run, member_id, state="COMPLETED"); run["active"] = None; ev["done"] = True
        return ev

    if act in ("finish", "end"):
        # "finish" is the agent saying its part is done; "end" is the harness ending the visit (ENDED). The outcome says which, and a manager is
        # told the outcome, so a member that ran out of turns is not taken for one that finished.
        outcome = "completed" if act == "finish" else action.get("outcome") or "exhausted_turns"
        _done_with_inbox(rd, member_id); _outcome(run, member_id, node.get("id"), outcome, inv)
        _set_member(run, member_id, state="COMPLETED")
        run["active"] = None
        ev["reason"] = action.get("reason") or action.get("text") or "done"; ev["outcome"] = outcome
        # Pod workers report RESULT to their manager (does not steal the workflow cursor).
        from .context import manager_of, pod_of
        mgr = manager_of(bundle, member_id)
        if mgr:
            mailbox.send(rd, member_id, mgr, "RESULT", {"text": ev["reason"], "outcome": outcome, "member": member_id,
                                                        "pod": (pod_of(bundle, member_id) or {}).get("id")}, inv=inv)
            ev["notified"] = mgr
        return ev

    if act == "fail":
        _outcome(run, member_id, node.get("id"), "failed", inv)
        _set_member(run, member_id, state="FAILED")
        run["status"] = "FAILED"; run["error"] = action.get("reason") or action.get("text") or "failed"
        run["active"] = None
        return ev

    if act == "noop":
        return ev

    raise ScheduleError(f"unknown action {act}")

GATE_RETRIES = 2          # a gate may send the run back this many times (a node's "max_retries" overrides it)

def _done_with_inbox(rd, member_id):
    """A member that ends its part has dealt with the findings and results sent to it: its next visit shows only what arrives after."""
    mailbox.mark_read(rd, [m["id"] for m in mailbox.inbox(rd, member_id) if m.get("type") in ("FINDING", "RESULT") and m.get("state") != "read"])

def _gate(bundle, proj, run, node):
    """Run the gate's check (pipeline/gates.py) and keep what it showed in run["gates"]."""
    when, detail = gates.run(proj, bundle, node)
    rec = run.setdefault("gates", {}).setdefault(node["id"], {"fails": 0})
    rec.update(check=node.get("check") or "approve", result=when, detail=detail[:4000])
    if when == "fail": rec["fails"] += 1
    return when, detail

def _join_ready(bundle, run, join_id):
    """True when every immediate run predecessor of join is listed in completed_nodes."""
    preds = [e["from"] for e in (bundle.get("workflow") or {}).get("edges") or [] if e.get("to") == join_id]
    nodes = R.node_map(bundle)
    done = set(run.get("completed_nodes") or [])
    for pid in preds:
        n = nodes.get(pid) or {}
        if n.get("type") == "run" and pid not in done: return False
    return True

def run_budget_s(proj, bundle):
    """The whole run's wall-clock budget in seconds (project limits.max_minutes, else PIPELINE_MINUTES), or 0 for none."""
    lim = ((bundle.get("project") or {}).get("limits") or {}).get("max_minutes")
    return float(lim if lim else proj.get("PIPELINE_MINUTES", 0, float) or 0) * 60

def step(proj, *, chat_fn=None, bundle=None):
    """Advance the pipeline by one harness decision and at most one model call. Returns an event dict. The time the step took (model call, tools,
    gates) is added to the run's `used_s`, which is saved, so a restart does not refund it."""
    t0 = time.time(); ev = _step(proj, chat_fn=chat_fn, bundle=bundle); run = ev.get("run")
    if run is not None and not ev.get("idle") and ev.get("status") not in ("BUSY", "WAITING_APPROVAL"):
        run["used_s"] = round(float(run.get("used_s") or 0) + time.time() - t0, 2); R.save_run(proj, run)
    return ev

def _step(proj, *, chat_fn=None, bundle=None):
    if bundle is None:
        bundle, ref = R.load_bundle(proj)
    else:
        ref = None
    run = R.ensure_run(proj, bundle, pipeline_ref=ref)
    if run["status"] in TERMINAL:
        return {"status": run["status"], "idle": True, "run": run}
    if run["step_count"] >= run.get("max_steps", 200):
        run["status"] = "FAILED"; run["error"] = "max_steps"; R.save_run(proj, run)
        return {"status": "FAILED", "error": "max_steps", "run": run}
    budget = run_budget_s(proj, bundle)
    if budget and float(run.get("used_s") or 0) >= budget and run["status"] != "WAITING_APPROVAL":
        # out of time is its own ending: not a failure of the work, and never a completion
        for mid, m in run["members"].items():
            if m.get("state") in ("READY", "RUNNING", "WAITING"): _outcome(run, mid, m.get("node"), "exhausted_time"); m["state"] = "SUSPENDED"
        run["status"] = "EXHAUSTED"; run["active"] = None; run["result"] = "exhausted_time"
        run["error"] = f"the run's time budget of {budget / 60:g} minutes is used up"; R.save_run(proj, run)
        return {"status": "EXHAUSTED", "error": run["error"], "run": run}

    nodes = R.node_map(bundle)
    rd = run_dir(proj); os.makedirs(rd, exist_ok=True)

    # A member still RUNNING here was in a model call when the process died (a step is one process). It takes that turn again, with its turns
    # so far (turns.jsonl); before 2026-11-11 the cursor's member was woken instead, which for an agent answering a question was the asker.
    for mid, m in run["members"].items():
        if m.get("state") == "RUNNING":
            m["state"] = "READY"; run["active"] = mid; run["recovered"] = int(run.get("recovered") or 0) + 1

    # Prefer answering READY members who have pending questions / were set active
    active = run.get("active")
    if active and (run["members"].get(active) or {}).get("state") == "READY":
        node_id = run["members"][active].get("node") or run.get("cursor")
        node = nodes.get(node_id) or {"id": node_id, "type": "run", "agent": active}
        return _invoke_member(bundle, proj, run, active, node, chat_fn)

    # Resume WAITING asker if their question was answered
    for mid, m in list(run["members"].items()):
        if m.get("state") == "WAITING" and m.get("wait_for"):
            answered = any(msg.get("type") == "ANSWER" and msg.get("reply_to") == m["wait_for"]
                           for msg in mailbox.iter_messages(rd))
            if answered:
                _set_member(run, mid, state="READY", wait_for=None)
                run["active"] = mid
                node = nodes.get(m.get("node")) or {"id": m.get("node"), "type": "run", "agent": mid}
                return _invoke_member(bundle, proj, run, mid, node, chat_fn)

    cursor = run.get("cursor")
    node = nodes.get(cursor)
    if not node:
        run["status"] = "FAILED"; run["error"] = f"missing node {cursor}"; R.save_run(proj, run)
        return {"status": "FAILED", "error": run["error"], "run": run}

    typ = node.get("type")

    if typ == "start":
        nxt = _advance(bundle, run, cursor)
        run["cursor"] = nxt or cursor
        run["step_count"] += 1; run["steps_done"].append({"id": f"start-{run['step_count']}", "node": cursor})
        R.save_run(proj, run)
        return {"status": run["status"], "event": "start", "next": nxt, "run": run}

    if typ == "end":
        run["status"] = "COMPLETED"; run["active"] = None; run["result"] = _result(run); run["fit_actual"] = _fit_actual(bundle, proj, run)
        run["step_count"] += 1; R.save_run(proj, run)
        return {"status": "COMPLETED", "event": "end", "result": run["result"], "run": run}

    if typ == "fan_out":
        nxt = _advance(bundle, run, cursor)
        run["cursor"] = nxt or cursor
        # mark join pending from edge targets that are run nodes
        run["step_count"] += 1; R.save_run(proj, run)
        return {"status": run["status"], "event": "fan_out", "next": nxt, "queue": list(run.get("queue") or []), "run": run}

    if typ == "join":
        if not _join_ready(bundle, run, cursor):
            # try to run next queued branch
            nxt = _pop_queue(bundle, run)
            if nxt:
                run["cursor"] = nxt; R.save_run(proj, run)
                return {"status": run["status"], "event": "join_wait", "next": nxt, "run": run}
            if _deadlock(run):
                run["status"] = "FAILED"; run["error"] = "deadlock"; R.save_run(proj, run)
                return {"status": "FAILED", "error": "deadlock", "run": run}
            R.save_run(proj, run)
            return {"status": run["status"], "event": "join_wait", "run": run}
        run["completed_nodes"] = []  # clear for a later fan_out/join pair
        nxt = _advance(bundle, run, cursor)
        run["cursor"] = nxt or cursor
        run["step_count"] += 1; R.save_run(proj, run)
        return {"status": run["status"], "event": "join", "next": nxt, "run": run}

    if typ == "loop":
        lim = (node.get("limits") or {}).get("max_iterations") if isinstance(node.get("limits"), dict) else node.get("max_iterations")
        lim = int(lim or 1)
        count = int(run["loop_counts"].get(cursor, 0))
        if count < lim and run.get("last_gate") != "force_done":
            run["loop_counts"][cursor] = count + 1
            nxt = _advance(bundle, run, cursor, when="continue")
            if not nxt: nxt = _advance(bundle, run, cursor)
        else:
            nxt = _advance(bundle, run, cursor, when="done")
        run["cursor"] = nxt or cursor
        run["step_count"] += 1; R.save_run(proj, run)
        return {"status": run["status"], "event": "loop", "count": run["loop_counts"].get(cursor), "next": nxt, "run": run}

    if typ == "approve" or (typ == "gate" and node.get("check") == "approve"):
        # A person's decision, for this arrival at this node only. Without PIPELINE_AUTO_APPROVE=1 in the project's settings the run waits.
        seq = int(run.setdefault("approve_seq", {}).get(cursor, 0)); key = f"{cursor}#{seq}"
        dec = run.setdefault("approvals", {}).get(key)
        if not dec and proj.get("PIPELINE_AUTO_APPROVE", "0") == "1":
            dec = run["approvals"][key] = {"decision": "approved", "by": "auto (PIPELINE_AUTO_APPROVE=1)", "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if not dec:
            run["status"] = "WAITING_APPROVAL"; run["approve_node"] = cursor
            R.save_run(proj, run)
            return {"status": "WAITING_APPROVAL", "event": "approve", "node": cursor,
                    "message": f"waiting for a person: codebot pipeline approve {proj.name}   (or: codebot pipeline reject {proj.name} REASON)", "run": run}
        run["approve_seq"][cursor] = seq + 1; run.pop("approve_node", None); run["status"] = "READY"
        when = "pass" if dec["decision"] == "approved" else "fail"; run["last_gate"] = when
        nxt = _advance(bundle, run, cursor, when=when)
        if when == "fail" and not nxt:
            run["status"] = "FAILED"; run["result"] = "rejected"; run["error"] = f"rejected at approval {cursor}" + (f": {dec['reason']}" if dec.get("reason") else "")
        else: run["cursor"] = nxt or cursor
        run["step_count"] += 1; R.save_run(proj, run)
        return {"status": run["status"], "event": "approve", "when": when, "by": dec.get("by"), "next": nxt, "run": run}

    if typ == "gate":
        when, detail = _gate(bundle, proj, run, node)
        run["last_gate"] = when
        if when == "fail": run["feedback"] = {"gate": cursor, "detail": detail[:3000]}      # for whoever the fail edge sends the work back to (_task_text)
        else: run.pop("feedback", None)
        fails = run.get("gates", {}).get(cursor, {}).get("fails", 0)
        if when == "fail" and fails > int(node.get("max_retries", GATE_RETRIES)) and node.get("on_exhausted") == "pass":
            first = detail.splitlines()[0] if detail else ""          # an opinion gate (QA's tests): the run goes on and says so
            run.setdefault("warnings", []).append(f"gate {cursor} still failed after {fails} tries and was passed over: {first}")
            when = "pass"; run.pop("feedback", None)
        nxt = _advance(bundle, run, cursor, when=when)
        if when == "fail" and (not nxt or fails > int(node.get("max_retries", GATE_RETRIES))):
            first = detail.splitlines()[0] if detail else ""
            run["status"] = "FAILED"; run["error"] = f"gate {cursor} failed" + (f" {fails} times" if nxt else "") + (f": {first}" if first else "")
            nxt = None
        else: run["cursor"] = nxt or cursor
        run["step_count"] += 1; R.save_run(proj, run)
        return {"status": run["status"], "event": "gate", "when": when, "detail": detail[:2000], "next": nxt, "run": run}

    if typ == "run":
        # if this run node finished, advance
        mid = node.get("agent")
        st = (run["members"].get(mid) or {}).get("state")
        if st == "COMPLETED" and run.get("cursor") == node["id"] and run.get("active") is None:
            if _checkpoint(bundle, proj, run, node, mid) == "retry":
                _set_member(run, mid, state="SUSPENDED")           # the next step sends the work back to it, with what it broke
                run["step_count"] += 1; R.save_run(proj, run)
                return {"status": run["status"], "event": "regression", "member": mid, "detail": run["feedback"]["detail"][:600], "run": run}
            done = list(run.get("completed_nodes") or [])
            if node["id"] not in done: done.append(node["id"]); run["completed_nodes"] = done
            nxt = _advance(bundle, run, cursor)
            if nxt:
                run["cursor"] = nxt
                _set_member(run, mid, state="SUSPENDED")
            run["step_count"] += 1; R.save_run(proj, run)
            return {"status": run["status"], "event": "run_done", "member": mid, "next": nxt, "run": run}
        if (node.get("action") or "") not in SPEC_WORK: frozen.freeze(proj, R.workspace_of(proj, bundle))      # the build starts: the spec is fixed
        if node.get("action") == "fix" and not run.get("feedback") and not any(
                m.get("type") == "FINDING" and m.get("state") != "read" and (m.get("payload") or {}).get("findings") for m in mailbox.inbox(rd, mid)):
            # nobody found anything for this member to fix: no model call (the first real builder spent 8 calls re-reading a program with no findings)
            _done_with_inbox(rd, mid); _set_member(run, mid, state="COMPLETED", node=node["id"]); run["active"] = None
            _outcome(run, mid, node["id"], "skipped"); run.setdefault("skipped", []).append(node["id"]); run["step_count"] += 1; R.save_run(proj, run)
            return {"status": run["status"], "event": "skip", "node": node["id"], "why": "no findings to fix", "run": run}
        _activate_run_node(bundle, run, node)
        return _invoke_member(bundle, proj, run, mid, node, chat_fn)

    if typ == "handoff":
        nxt = _advance(bundle, run, cursor)
        run["cursor"] = nxt or cursor
        run["step_count"] += 1; R.save_run(proj, run)
        return {"status": run["status"], "event": "handoff", "next": nxt, "run": run}

    raise ScheduleError(f"unhandled node type {typ}")

SPEC_WORK = ("write_spec", "merge_findings")          # run-node actions that still write the spec; any other action freezes it first
REGRESSION_RETRIES = 1                                # how many times work that broke a passing example is sent back before it is set aside

def owned_tree(proj, ws):
    """(True, "") when the code directory is one codebot made for this project and may commit to and reset: `codebot start` prepared it
    (.git-prepped) AND it is on the project's own branch codebot/<name> or lies under codebot's workspaces/ or bench/runs/. Otherwise
    (False, why): the user's own checkout is never committed to, reset or cleaned."""
    if not gitops.is_repo(ws): return False, "the code directory is not a git repository"
    if not os.path.isfile(proj.p(".git-prepped")): return False, "codebot did not prepare this code directory (it was not started with codebot start)"
    branch = gitops.git(ws, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip(); real = os.path.realpath(ws)      # also right before the first commit
    inside = any(real.startswith(os.path.realpath(os.path.join(proj.home, d)) + os.sep) for d in ("workspaces", os.path.join("bench", "runs")))
    if branch == f"codebot/{proj.name}" or inside: return True, ""
    return False, f"the code directory is on branch {branch!r}, not codebot/{proj.name}, and is not one of codebot's own worktrees"

def _set_aside(proj, run, ws, rd, node, mid, to_commit, reason):
    """Take the code back to to_commit, keeping what is there now: a snapshot commit under refs/codebot/set-aside/ (tracked and untracked files;
    `git checkout <ref> -- .` restores it) and the same change as a patch artifact. The reset happens only after the snapshot exists.
    Returns the rollback record, or None with the reason appended to the run's warnings when nothing was reset."""
    ok, why = owned_tree(proj, ws)
    if not ok or not to_commit:
        run.setdefault("warnings", []).append(f"{mid}'s work on {node['id']} was kept although {reason}: no rollback, because " + (why or "there is no checkpoint to go back to"))
        return None
    n = len(run.setdefault("rollbacks", [])) + 1; ref = f"refs/codebot/set-aside/{node['id']}-{n}"
    gitops.git(ws, "add", "-A")
    tree = gitops.git(ws, "write-tree").stdout.strip()
    snap = gitops.git(ws, "commit-tree", tree, "-p", "HEAD", "-m", f"codebot: set aside {mid}'s work on {node['id']}: {reason}").stdout.strip() if tree else ""
    if snap: gitops.git(ws, "update-ref", ref, snap)
    if not snap or gitops.git(ws, "rev-parse", "--verify", "-q", ref).stdout.strip() != snap:
        gitops.git(ws, "reset", "-q")                  # only the index: the files stay
        run["warnings"] = run.get("warnings", []) + [f"{mid}'s work on {node['id']} was kept although {reason}: the snapshot could not be made, so nothing was reset"]
        return None
    patch = gitops.git(ws, "diff", "--cached", "--binary", to_commit).stdout
    art = artifacts.put(rd, f"set-aside-{node['id']}", patch, producer=mid, kind="patch")
    gitops.git(ws, "reset", "-q", "--hard", to_commit, check=True); gitops.git(ws, "clean", "-fdq")
    rec = {"node": node["id"], "member": mid, "reason": reason, "ref": ref, "snapshot": snap, "artifact": art["id"], "to": to_commit,
           "inv": run["members"][mid].get("done_inv"), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    run["rollbacks"].append(rec)
    return rec

def _checkpoint(bundle, proj, run, node, mid):
    """After a member that may write finishes a run node: run the frozen spec's examples. If an example that passed at the last checkpoint fails
    now, the work goes back to that member ("retry") with what broke, at most PIPELINE_REGRESSION_RETRIES times (default 1); after that it is
    set aside (_set_aside) and the code returns to the last checkpoint. Otherwise the state is committed and becomes the checkpoint. Commits and
    resets happen only in a tree codebot owns (owned_tree)."""
    ws = R.workspace_of(proj, bundle); agent = member_agent(bundle, mid) or {}; rd = run_dir(proj)
    if not frozen.info(proj) or not (agent.get("sees") or {}).get("write"): return None
    try: j = gates.judge(proj, ws)
    except Exception as e:
        run.setdefault("warnings", []).append(f"no checkpoint after {node['id']}: the examples could not be run ({type(e).__name__}: {e})"); return None
    if not j["total"]: return None
    prev = run.get("checkpoint") or {}
    if prev.get("failing") is not None and j["failing"] is not None: broke = sorted(set(j["failing"]) - set(prev["failing"]))
    else: broke = ["?"] * max(0, int(prev.get("passed") or 0) - j["passed"])
    if broke:
        what = (f"{len(broke)} of the spec's examples that passed before {mid}'s work on `{node['id']}` fail now "
                f"({prev.get('passed')} of {j['total']} passed, now {j['passed']}).\n{j['detail'][:2500]}")
        sent_back = int(run.setdefault("regressions", {}).get(node["id"], 0)); allowed = proj.get("PIPELINE_REGRESSION_RETRIES", REGRESSION_RETRIES, int)
        if sent_back < allowed:
            run["regressions"][node["id"]] = sent_back + 1
            run["feedback"] = {"gate": "regression", "title": "Your last changes broke examples that passed before. Fix this", "detail": what}
            return "retry"
        times = "twice" if sent_back == 1 else f"{sent_back + 1} times"
        rec = _set_aside(proj, run, ws, rd, node, mid, prev.get("commit"), f"it broke passing examples {times}")
        if rec:
            note = f"{mid}'s work on {node['id']} broke passing examples {times} and was set aside (artifact {rec['artifact']}); the code is back at the last checkpoint"
            run.setdefault("warnings", []).append(note)
        else: note = run["warnings"][-1]
        _outcome(run, mid, node["id"], "failed")
        run["feedback"] = {"gate": "regression", "title": "Earlier work was a problem", "detail": note + ".\n" + what}
        return None
    if (run.get("feedback") or {}).get("gate") == "regression": run.pop("feedback")
    commit = gitops.commit_all(ws, f"pipeline: {mid} finished {node['id']} ({j['passed']} of {j['total']} spec examples pass)") if owned_tree(proj, ws)[0] else None
    run["checkpoint"] = {"node": node["id"], "member": mid, "passed": j["passed"], "total": j["total"], "failing": j["failing"], "commit": commit}
    run.setdefault("progress", []).append({k: run["checkpoint"][k] for k in ("node", "member", "passed", "total", "commit")})
    return None

def _result(run):
    """What a COMPLETED run can claim. "verified": its last examples or tests gate passed. "approved_unverified": a person approved and no
    mechanical check passed (a spec without runnable examples). "unverified": neither."""
    mech = [g for g in (run.get("gates") or {}).values() if g.get("check") in ("examples", "tests")]
    if mech and all(g.get("result") == "pass" for g in mech): return "verified"
    if any(a.get("decision") == "approved" and not str(a.get("by", "")).startswith("auto") for a in (run.get("approvals") or {}).values()): return "approved_unverified"
    return "unverified"

def _usage(run, mid, out):
    u = run.setdefault("usage", {}).setdefault(mid, {"calls": 0, "prompt_tokens": 0, "gen_tokens": 0, "max_prompt_tokens": 0, "model_s": 0.0})
    pt, gt = int(out.get("prompt_tokens") or 0), int(out.get("gen_tokens") or 0)
    u["calls"] += 1; u["prompt_tokens"] += pt; u["gen_tokens"] += gt; u["max_prompt_tokens"] = max(u["max_prompt_tokens"], pt)
    u["model_s"] = round(u["model_s"] + float(out.get("secs") or 0), 1)

def _fit_actual(bundle, proj, run):
    """What the Team Designer's estimate (run["fit"]) can be held against: the source the run produced and the largest prompt any member read."""
    from codebot import drafts
    ws = R.workspace_of(proj, bundle); lines = 0
    for n in drafts.spec_files(frozen.text(proj, ws)):
        try:
            with open(os.path.join(ws, n), encoding="utf-8", errors="replace") as f: lines += sum(1 for _ in f)
        except OSError: pass
    use = run.get("usage") or {}
    return {"lines": lines, "max_prompt_tokens": max((u["max_prompt_tokens"] for u in use.values()), default=0),
            "prompt_tokens": sum(u["prompt_tokens"] for u in use.values()), "gen_tokens": sum(u["gen_tokens"] for u in use.values()),
            "calls": sum(u["calls"] for u in use.values())}

def _activate_run_node(bundle, run, node):
    mid = node.get("agent")
    _set_member(run, mid, state="READY", node=node["id"])
    run["members"][mid]["visit"] = int(run["members"][mid].get("visit") or 0) + 1      # its turns (tools.history) start again with each visit
    run["members"][mid]["visit_s"] = 0.0; run["members"][mid].pop("outcome", None)
    run["active"] = mid
    run["cursor"] = node["id"]

def _drafts_bridge(proj, run, member_id, node):
    """One drafts.step under a pipeline project (temporary WORKFLOW=drafts in proj.cfg)."""
    from codebot import drafts, jobs
    prev = proj.cfg.get("WORKFLOW")
    proj.cfg["WORKFLOW"] = "drafts"
    # SPEC.md must exist in state or workdir — copy from workspace if needed
    ws = R.workspace_of(proj)
    spec_ws = os.path.join(ws, "SPEC.md")
    spec_st = proj.p("SPEC.md")
    if not os.path.isfile(spec_st) and os.path.isfile(spec_ws):
        os.makedirs(proj.state, exist_ok=True)
        with open(spec_ws, encoding="utf-8") as f: text = f.read()
        with open(spec_st, "w", encoding="utf-8") as f: f.write(text)
    try:
        hold = jobs.Hold(proj.home, "pipeline", proj.name, wait=1.0)
    except jobs.Busy as e:
        proj.cfg["WORKFLOW"] = prev or "pipeline"
        run["members"][member_id]["state"] = "READY"
        R.save_run(proj, run)
        return {"status": "BUSY", "owner": e.owner, "run": run}
    try:
        ev = drafts.step(proj)
    except Exception as e:
        hold.release(); proj.cfg["WORKFLOW"] = prev or "pipeline"
        run["members"][member_id]["state"] = "FAILED"
        run["status"] = "FAILED"; run["error"] = str(e)
        R.save_run(proj, run)
        return {"status": "FAILED", "error": str(e), "run": run}
    hold.release()
    proj.cfg["WORKFLOW"] = prev or "pipeline"
    kind = (ev or {}).get("event")
    run["step_count"] += 1
    run["steps_done"].append({"id": f"drafts-{run['step_count']}", "member": member_id, "drafts": kind, "node": node.get("id")})
    run["last"] = {"member": member_id, "action": "drafts", "drafts": kind}
    if kind in ("all_done", "finished_incomplete"):
        _set_member(run, member_id, state="COMPLETED"); run["active"] = None
    elif kind in ("error",) and "model" not in str((ev or {}).get("error", "")).lower():
        _set_member(run, member_id, state="FAILED")
        run["status"] = "FAILED"; run["error"] = (ev or {}).get("error") or "drafts error"
        run["active"] = None
    else:
        _set_member(run, member_id, state="READY"); run["active"] = member_id
    R.save_run(proj, run)
    return {"status": run["status"], "event": "drafts", "result": ev, "run": run}

def _left_s(proj, bundle, run, m, agent):
    """Seconds this member may still spend now: what is left of its visit's budget (limits.max_minutes) and of the run's, or None for no limit."""
    left = []
    vis = float((agent.get("limits") or {}).get("max_minutes") or 0) * 60
    if vis: left.append(vis - float(m.get("visit_s") or 0))
    tot = run_budget_s(proj, bundle)
    if tot: left.append(tot - float(run.get("used_s") or 0))
    return max(0.0, min(left)) if left else None

def _invoke_member(bundle, proj, run, member_id, node, chat_fn):
    """One turn of a member: decide an action (the model, or the harness when the visit is over), carry it out, record it. Every stage is
    journalled under the turn's invocation id (journal.py), so a turn the process died in is finished without asking the model again and
    without doing anything twice."""
    from codebot import jobs, ollama
    rd = run_dir(proj); ws = R.workspace_of(proj, bundle)
    agent = member_agent(bundle, member_id); m = run["members"][member_id]
    if (node.get("action") or "") == "drafts" and chat_fn is None:
        m["state"] = "RUNNING"; m["steps"] = int(m.get("steps") or 0) + 1
        R.save_run(proj, run)
        return _drafts_bridge(proj, run, member_id, node)
    back = frozen.restore(proj, ws)
    if back: run.setdefault("warnings", []).append(f"{', '.join(back)} had been changed in the code directory after the spec was frozen and was put back")
    visit = int(m.get("visit") or 0)
    asked = [q for q in mailbox.inbox(rd, member_id) if q.get("type") == "QUESTION" and q.get("state") != "read"]
    acts = tools.actions(agent, has_question=bool(asked))
    limit = int((agent.get("limits") or {}).get("max_turns") or tools.MAX_TURNS); minutes = float((agent.get("limits") or {}).get("max_minutes") or 0)
    pend = journal.unfinished(rd, member_id, m.get("done_inv")); replay = bool(pend); forced = None
    if replay: inv, action, forced = pend["inv"], pend["action"], pend.get("forced")
    else:
        inv = journal.new_id()
        if not asked:                 # a member that owes an answer always gets its turn
            if tools.done_after_check(rd, member_id, visit): forced = "checked"
            elif tools.refused_in_a_row(rd, member_id, visit): forced = "exhausted_refusals"
            elif len(tools.turns(rd, member_id, visit)) >= limit: forced = "exhausted_turns"
            elif minutes and float(m.get("visit_s") or 0) >= minutes * 60: forced = "exhausted_time"
    m["state"] = "RUNNING"; m["steps"] = int(m.get("steps") or 0) + 1; m["inv"] = inv
    R.save_run(proj, run)
    # global inference lock
    try:
        hold = jobs.Hold(proj.home, "pipeline", proj.name, wait=1.0)
    except jobs.Busy as e:
        m["state"] = "READY"
        R.save_run(proj, run)
        return {"status": "BUSY", "owner": e.owner, "run": run}
    t0 = time.time(); left = _left_s(proj, bundle, run, m, agent); given = ()
    try:
        if not replay:
            journal.event(rd, inv, "prepared", member=member_id, node=node.get("id"), visit=visit)
            _fault("prepared")
            if forced:               # no model call: the visit ends here and whatever checks the work (a gate) decides
                action = {"action": "end", "outcome": forced, "reason": ENDED[forced].format(turns=limit, minutes=minutes)}
                if forced != "checked": run.setdefault("warnings", []).append(f"{member_id} on node {node.get('id')}: {action['reason']}")
            else:
                task, extra = _task_text(bundle, proj, node, agent, run); given = tuple(extra)
                try:
                    action, agent, raw, used = invoke.invoke(bundle, rd, member_id, task, proj=proj, chat_fn=chat_fn, extra_files=extra or None, acts=acts,
                                                             history=tools.history(rd, member_id, visit, agent.get("memory") or "task", given),
                                                             timeout=None if left is None else max(30, min(900, left + 60)))
                    _usage(run, member_id, used)
                except invoke.InvokeError as e: action = {"action": "invalid", "problem": f"your reply could not be used: {e}"}
                except ollama.ModelError as e: action = {"action": "invalid", "problem": f"the model call failed: {e}"}
            journal.event(rd, inv, "decided", action=action, forced=forced)
            _fault("decided")
        # A request the harness cannot carry out is the agent's to correct on its next turn, not the end of the run.
        act = action.get("action"); ev = {"member": member_id, "action": act, "node": node.get("id")}
        prior = next((r for r in tools.turns(rd, member_id, visit) if r.get("inv") == inv), None) if replay else None
        sha = tools.fingerprint(ws, agent, action) if act not in ("end", "invalid") else None
        same = tools.unchanged(rd, ws, agent, member_id, visit, action, sha, given) if not replay and act not in ("end", "invalid") else None
        if act == "invalid": ev["error"] = action.get("problem") or "your reply could not be used"
        elif act == "end": ev = apply_action(bundle, proj, run, member_id, agent, action, node, inv=inv)
        elif act not in acts and act != "noop": ev["error"] = f"you may not use {act!r} here; your actions are: {', '.join(acts)}"
        elif replay and act == "run":
            if prior: ev["result"] = prior.get("result") or ""
            else:           # it may have run, in part or whole: running it again could do its work twice
                ev["error"] = "the process stopped while this command may have been running, and it was not run again: look at what state it left before you go on"
                run.setdefault("warnings", []).append(f"{member_id}: a command of invocation {inv} may or may not have run before a restart; it was not repeated")
        elif same: ev["error"] = same
        elif not replay and tools.repeated(rd, member_id, visit, action):
            ev["error"] = "you ran exactly this on your previous turn and its output is above; do something else, or finish"
        else:
            try: ev = apply_action(bundle, proj, run, member_id, agent, action, node, inv=inv, cap_s=left)
            except (ScheduleError, ValidationError, tools.ToolError, OSError) as e:
                ev["error"] = "; ".join(e.errors) if isinstance(e, ValidationError) else str(e)
        _fault("effects")
        tools.record(rd, member_id, visit, node.get("id"), action, ev.get("result") or ("error: " + ev["error"] if ev.get("error") else ""), sha=sha, inv=inv)
        journal.event(rd, inv, "applied")
        _fault("applied")
        if len(ev.get("result") or "") > 600: ev["result"] = ev["result"][:600] + " ... (whole in turns.jsonl)"
    except Exception as e:
        m["state"] = "FAILED"; _outcome(run, member_id, node.get("id"), "failed", inv)
        run["status"] = "FAILED"; run["error"] = str(e)
        R.save_run(proj, run)
        return {"status": "FAILED", "error": str(e), "run": run}
    finally: hold.release()             # however the call ends, an interrupt included: the lock must not outlive it
    m["visit_s"] = round(float(m.get("visit_s") or 0) + time.time() - t0, 2); m["done_inv"] = inv; m.pop("inv", None)
    if replay: run["recovered_actions"] = int(run.get("recovered_actions") or 0) + 1
    run["step_count"] += 1
    run["steps_done"].append({"id": inv, "member": member_id, "action": action.get("action"), "node": node.get("id"), **({"outcome": ev["outcome"]} if ev.get("outcome") else {})})
    run["last"] = ev
    # if finished this run node and not waiting, leave active clear so next step advances
    if m["state"] == "COMPLETED" and not run.get("active"):
        pass
    elif m["state"] == "WAITING":
        pass
    elif m["state"] == "RUNNING":
        # anything but finish / findings / a question: another turn on the same node
        m["state"] = "READY"
        run["active"] = member_id
    R.save_run(proj, run)
    journal.event(rd, inv, "committed")
    if _deadlock(run):
        run["status"] = "FAILED"; run["error"] = "deadlock"; R.save_run(proj, run)
    return {"status": run["status"], "event": "invoke", "result": ev, "run": run}

def _task_text(bundle, proj, node, agent, run=None):
    bits = [f"Workflow node `{node.get('id')}` action={node.get('action') or 'work'}."]
    if node.get("description"): bits.append(str(node["description"]))
    bits.append(f"Your goal: {agent.get('goal') or ''}")
    ws = R.workspace_of(proj, bundle)
    extra = {}
    spec = os.path.join(ws, "SPEC.md")
    if os.path.isfile(spec):
        try:
            # temporary grant for reading SPEC if agent can read SPEC.md or **
            if scope.can_read(agent, "SPEC.md"):
                extra["SPEC.md"] = scope.read_text(ws, agent, "SPEC.md", limit=20000)
        except ValidationError:
            pass
    sees = agent.get("sees") or {}
    if sees.get("write"): bits.append("You may write: " + ", ".join(sees["write"]) + ".")
    if sees.get("read") and os.path.isdir(ws): bits.append("Files in the code directory:\n" + tools.listing(ws, agent, limit=40))
    fb = (run or {}).get("feedback")
    if fb and sees.get("write"):
        bits.append((fb.get("title") or f"The harness checked the result (gate `{fb['gate']}`) and it FAILED. Fix this") + f":\n{fb['detail']}")
    rd = run_dir(proj)
    qs = [m for m in mailbox.inbox(rd, agent["member_id"]) if m.get("type") == "QUESTION" and m.get("state") != "read"]
    if qs:
        bits.append("You have unanswered QUESTION(s). Prefer action=answer with question_id.")
        for q in qs[-3:]:
            bits.append(f"  question_id={q['id']} from={q['from']}: {(q.get('payload') or {}).get('text', '')[:300]}")
    return "\n".join(bits), extra

def run_until_idle(proj, *, chat_fn=None, max_steps=100, bundle=None):
    """Call step() until COMPLETED/FAILED/CANCELLED/BUSY or max_steps."""
    events = []
    for _ in range(max_steps):
        ev = step(proj, chat_fn=chat_fn, bundle=bundle)
        events.append({k: v for k, v in ev.items() if k != "run"})
        st = ev.get("status")
        if st in TERMINAL or st in ("BUSY", "WAITING_APPROVAL") or ev.get("idle"):
            return ev.get("run") or R.load_run(proj), events
    return R.load_run(proj), events

def cancel(proj):
    """Mark the durable run CANCELLED. The bash loop exits on that status."""
    run = R.load_run(proj)
    if not run:
        return {"status": "idle", "run": None}
    if run.get("status") in TERMINAL:
        return {"status": run["status"], "run": run}
    run["status"] = "CANCELLED"; run["result"] = "cancelled"
    run["active"] = None
    for mid, m in (run.get("members") or {}).items():
        if m.get("state") in ("READY", "RUNNING", "WAITING"):
            _outcome(run, mid, m.get("node"), "cancelled"); m["state"] = "CANCELLED"
    R.save_run(proj, run)
    return {"status": "CANCELLED", "run": run}

def approve(proj, decision="approved", reason="", by="user"):
    """A person's decision at the approval the run is waiting at. It is recorded for that arrival at that node and nothing else: a later
    approval node, or the same node reached again, waits again. Nothing is recorded when the run is not waiting."""
    run = R.load_run(proj)
    if not run: return {"status": "idle", "run": None}
    if run.get("status") != "WAITING_APPROVAL" or not run.get("approve_node"):
        return {"status": run.get("status"), "error": "nothing is waiting for approval", "run": run}
    node = run["approve_node"]; key = f"{node}#{int((run.get('approve_seq') or {}).get(node, 0))}"
    run.setdefault("approvals", {})[key] = {"decision": decision, "by": by, "reason": reason, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    R.save_run(proj, run)
    return step(proj)                     # passes the approval node only; no model call

def reject(proj, reason="", by="user"):
    """The other decision: the run follows the node's `when: fail` edge, or ends FAILED (result "rejected") when it has none."""
    return approve(proj, decision="rejected", reason=reason, by=by)
