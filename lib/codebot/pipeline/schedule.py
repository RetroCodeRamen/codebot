"""Harness-owned sequential scheduler. At most one model invocation per step() call."""
import json, os
from . import artifacts, mailbox, scope, invoke, runtime as R
from .context import member_agent, may_talk
from .validate import ValidationError

class ScheduleError(Exception): pass

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
    edges = R.edges_from(bundle, from_id, when=when) or R.edges_from(bundle, from_id)
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

def apply_action(bundle, proj, run, member_id, agent, action, node):
    """Apply a structured action; may put agent WAITING and ready another. Returns event dict."""
    ws = R.workspace_of(proj, bundle)
    rd = run_dir(proj)
    act = action.get("action")
    ev = {"member": member_id, "action": act, "node": node.get("id")}

    if act in ("ask", "escalate"):
        to = action.get("to")
        if act == "escalate" and not to:
            from .context import escalate_target
            to = escalate_target(bundle, member_id)
        if not to or not may_talk(agent, to): raise ScheduleError(f"{member_id} cannot ask {to!r}")
        text = action.get("text") or ("escalation" if act == "escalate" else "")
        msg = mailbox.send(rd, member_id, to, "QUESTION", {"text": text, "escalate": act == "escalate"}, blocking=True)
        _set_member(run, member_id, state="WAITING", wait_for=msg["id"])
        _set_member(run, to, state="READY", node=run["members"].get(to, {}).get("node") or node.get("id"))
        run["active"] = to
        # queue the asker to resume later via answer
        ev["question_id"] = msg["id"]; ev["to"] = to
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
        mailbox.answer(rd, member_id, q, {"text": action.get("text") or ""})
        mailbox.mark_read(rd, [qid])
        asker = q["from"]
        _set_member(run, asker, state="READY", wait_for=None)
        _set_member(run, member_id, state="COMPLETED" if not mailbox.pending_questions(rd) else "READY")
        # prefer resume asker next
        run["active"] = asker
        ev["question_id"] = qid; ev["to"] = asker
        return ev

    if act == "findings":
        findings = action.get("findings") or [{"what": action.get("text") or "no details"}]
        text = json.dumps(findings, indent=2, ensure_ascii=False)
        meta = artifacts.put(rd, f"findings-{member_id}", text, producer=member_id, kind="findings")
        # notify architect-like talk targets with FINDING
        for to in agent.get("can_talk_to") or []:
            mailbox.send(rd, member_id, to, "FINDING", {"findings": findings, "artifact": meta["id"]})
        ev["artifact"] = meta["id"]
        _set_member(run, member_id, state="COMPLETED")
        run["active"] = None
        return ev

    if act == "write":
        path = action.get("path") or action.get("artifact_name") or "ARTIFACT.md"
        content = action.get("content") or action.get("text") or ""
        if path.startswith("artifacts/") or action.get("artifact_name"):
            meta = artifacts.put(rd, action.get("artifact_name") or path, content, producer=member_id, kind="doc")
            ev["artifact"] = meta["id"]
        else:
            scope.write_text(ws, agent, path, content)
            ev["path"] = path
        return ev

    if act == "finish":
        _set_member(run, member_id, state="COMPLETED")
        run["active"] = None
        ev["reason"] = action.get("reason") or action.get("text") or "done"
        # Pod workers report RESULT to their manager (does not steal the workflow cursor).
        from .context import manager_of, pod_of
        mgr = manager_of(bundle, member_id)
        if mgr:
            mailbox.send(rd, member_id, mgr, "RESULT",
                         {"text": ev["reason"], "member": member_id, "pod": (pod_of(bundle, member_id) or {}).get("id")})
            ev["notified"] = mgr
        return ev

    if act == "fail":
        _set_member(run, member_id, state="FAILED")
        run["status"] = "FAILED"; run["error"] = action.get("reason") or action.get("text") or "failed"
        run["active"] = None
        return ev

    if act == "noop":
        return ev

    raise ScheduleError(f"unknown action {act}")

def _gate(bundle, proj, node):
    check = node.get("check") or "approve"
    ws = R.workspace_of(proj, bundle)
    if check == "approve":
        # auto-approve unless APPROVE=0 in project env for tests that want blocking
        return "pass" if proj.get("PIPELINE_AUTO_APPROVE", "1") != "0" else "fail"
    if check == "examples":
        spec_path = os.path.join(ws, "SPEC.md")
        if not os.path.isfile(spec_path): return "fail"
        from codebot import drafts
        with open(spec_path, encoding="utf-8") as f: spec = f.read()
        cov = drafts.coverage(spec)
        return "pass" if cov.get("examples", 0) > 0 else "fail"
    if check == "tests":
        return "pass" if os.path.isfile(os.path.join(ws, "verify.sh")) else "fail"
    return "pass"

def _join_ready(bundle, run, join_id):
    """True when every immediate run predecessor of join is listed in completed_nodes."""
    preds = [e["from"] for e in (bundle.get("workflow") or {}).get("edges") or [] if e.get("to") == join_id]
    nodes = R.node_map(bundle)
    done = set(run.get("completed_nodes") or [])
    for pid in preds:
        n = nodes.get(pid) or {}
        if n.get("type") == "run" and pid not in done: return False
    return True

def step(proj, *, chat_fn=None, bundle=None):
    """Advance the pipeline by one harness decision and at most one model call. Returns an event dict."""
    if bundle is None:
        bundle, ref = R.load_bundle(proj)
    else:
        ref = None
    run = R.ensure_run(proj, bundle, pipeline_ref=ref)
    if run["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
        return {"status": run["status"], "idle": True, "run": run}
    if run["step_count"] >= run.get("max_steps", 200):
        run["status"] = "FAILED"; run["error"] = "max_steps"; R.save_run(proj, run)
        return {"status": "FAILED", "error": "max_steps", "run": run}

    nodes = R.node_map(bundle)
    rd = run_dir(proj); os.makedirs(rd, exist_ok=True)

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
        run["status"] = "COMPLETED"; run["active"] = None
        run["step_count"] += 1; R.save_run(proj, run)
        return {"status": "COMPLETED", "event": "end", "run": run}

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

    if typ in ("gate", "approve"):
        if typ == "approve" and proj.get("PIPELINE_AUTO_APPROVE", "1") == "0" and not run.get("approved"):
            run["status"] = "WAITING_APPROVAL"
            run["approve_node"] = cursor
            R.save_run(proj, run)
            return {"status": "WAITING_APPROVAL", "event": "approve", "node": cursor, "run": run}
        when = _gate(bundle, proj, node) if typ == "gate" else "pass"
        if typ == "approve":
            run["approved"] = False  # consume one-shot approval for a later approve node
        run["last_gate"] = when
        nxt = _advance(bundle, run, cursor, when=when)
        if not nxt and when == "fail":
            run["status"] = "FAILED"; run["error"] = f"gate {cursor} failed"
        else:
            run["cursor"] = nxt or cursor
            if typ == "approve" and when == "pass":
                run["status"] = "RUNNING" if run.get("status") == "WAITING_APPROVAL" else run.get("status") or "RUNNING"
                run.pop("approve_node", None)
        run["step_count"] += 1; R.save_run(proj, run)
        return {"status": run["status"], "event": "gate" if typ == "gate" else "approve", "when": when, "next": nxt, "run": run}

    if typ == "run":
        # if this run node finished, advance
        mid = node.get("agent")
        st = (run["members"].get(mid) or {}).get("state")
        if st == "COMPLETED" and run.get("cursor") == node["id"] and run.get("active") is None:
            done = list(run.get("completed_nodes") or [])
            if node["id"] not in done: done.append(node["id"]); run["completed_nodes"] = done
            nxt = _advance(bundle, run, cursor)
            if nxt:
                run["cursor"] = nxt
                _set_member(run, mid, state="SUSPENDED")
            run["step_count"] += 1; R.save_run(proj, run)
            return {"status": run["status"], "event": "run_done", "member": mid, "next": nxt, "run": run}
        _activate_run_node(bundle, run, node)
        return _invoke_member(bundle, proj, run, mid, node, chat_fn)

    if typ == "handoff":
        nxt = _advance(bundle, run, cursor)
        run["cursor"] = nxt or cursor
        run["step_count"] += 1; R.save_run(proj, run)
        return {"status": run["status"], "event": "handoff", "next": nxt, "run": run}

    raise ScheduleError(f"unhandled node type {typ}")

def _activate_run_node(bundle, run, node):
    mid = node.get("agent")
    _set_member(run, mid, state="READY", node=node["id"])
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

def _invoke_member(bundle, proj, run, member_id, node, chat_fn):
    from codebot import jobs
    rd = run_dir(proj)
    agent = member_agent(bundle, member_id)
    if (node.get("action") or "") == "drafts" and chat_fn is None:
        run["members"][member_id]["state"] = "RUNNING"
        run["members"][member_id]["steps"] = int(run["members"][member_id].get("steps") or 0) + 1
        R.save_run(proj, run)
        return _drafts_bridge(proj, run, member_id, node)
    task, extra = _task_text(bundle, proj, node, agent)
    run["members"][member_id]["state"] = "RUNNING"
    run["members"][member_id]["steps"] = int(run["members"][member_id].get("steps") or 0) + 1
    R.save_run(proj, run)
    # global inference lock
    try:
        hold = jobs.Hold(proj.home, "pipeline", proj.name, wait=1.0)
    except jobs.Busy as e:
        run["members"][member_id]["state"] = "READY"
        R.save_run(proj, run)
        return {"status": "BUSY", "owner": e.owner, "run": run}
    try:
        action, agent, raw = invoke.invoke(bundle, rd, member_id, task, proj=proj, chat_fn=chat_fn, extra_files=extra or None)
        ev = apply_action(bundle, proj, run, member_id, agent, action, node)
    except Exception as e:
        hold.release()
        run["members"][member_id]["state"] = "FAILED"
        run["status"] = "FAILED"; run["error"] = str(e)
        R.save_run(proj, run)
        return {"status": "FAILED", "error": str(e), "run": run}
    hold.release()
    run["step_count"] += 1
    run["steps_done"].append({"id": f"inv-{run['step_count']}", "member": member_id, "action": action.get("action"), "node": node.get("id")})
    run["last"] = ev
    # if finished this run node and not waiting, leave active clear so next step advances
    if run["members"][member_id]["state"] == "COMPLETED" and not run.get("active"):
        pass
    elif run["members"][member_id]["state"] == "WAITING":
        pass
    elif action.get("action") in ("write", "noop") and run["members"][member_id]["state"] == "RUNNING":
        # keep READY for another turn on same node until finish
        run["members"][member_id]["state"] = "READY"
        run["active"] = member_id
    R.save_run(proj, run)
    if _deadlock(run):
        run["status"] = "FAILED"; run["error"] = "deadlock"; R.save_run(proj, run)
    return {"status": run["status"], "event": "invoke", "result": ev, "run": run}

def _task_text(bundle, proj, node, agent):
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
        if st in ("COMPLETED", "FAILED", "CANCELLED", "BUSY") or ev.get("idle"):
            return ev.get("run") or R.load_run(proj), events
    return R.load_run(proj), events

def cancel(proj):
    """Mark the durable run CANCELLED. The bash loop exits on that status."""
    run = R.load_run(proj)
    if not run:
        return {"status": "idle", "run": None}
    if run.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
        return {"status": run["status"], "run": run}
    run["status"] = "CANCELLED"
    run["active"] = None
    for mid, m in (run.get("members") or {}).items():
        if m.get("state") in ("READY", "RUNNING", "WAITING"):
            m["state"] = "CANCELLED"
    R.save_run(proj, run)
    return {"status": "CANCELLED", "run": run}

def approve(proj):
    """Human approval for an approve node (when PIPELINE_AUTO_APPROVE=0)."""
    run = R.load_run(proj)
    if not run:
        return {"status": "idle", "run": None}
    run["approved"] = True
    if run.get("status") == "WAITING_APPROVAL":
        run["status"] = "RUNNING"
    R.save_run(proj, run)
    # advance through the approve node immediately
    return step(proj)
