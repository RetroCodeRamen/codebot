"""Build per-invocation chat context according to the agent's memory policy."""
from . import mailbox, artifacts

def member_agent(bundle, member_id):
    for m in (bundle.get("team") or {}).get("members") or []:
        if m.get("id") == member_id:
            tmpl = (bundle.get("agents") or {}).get(m["agent"]) or {}
            # member overrides (can_talk_to, etc.) win over template for talk rights
            return {**tmpl, **{k: v for k, v in m.items() if k != "agent"}, "template_id": m["agent"], "member_id": member_id}
    return None

def link_kind(agent, other_id):
    """Return agent|repository|both for a declared member link, else None."""
    for link in agent.get("links") or []:
        if isinstance(link, dict) and link.get("to") == other_id:
            return link.get("kind") or "agent"
    return None

def may_talk(agent, other_id):
    """True if messaging is allowed: can_talk_to, or a link of kind agent/both."""
    if other_id in (agent.get("can_talk_to") or []):
        return True
    return link_kind(agent, other_id) in ("agent", "both")

def may_share_repo(agent, other_id):
    """True if a repository|both link declares shared-repo intent (sees still enforce paths)."""
    return link_kind(agent, other_id) in ("repository", "both")

def pods(bundle):
    return list((bundle.get("team") or {}).get("pods") or [])

def pod_of(bundle, member_id):
    """Return the first pod that lists member_id as a worker, or None."""
    for p in pods(bundle):
        if member_id in (p.get("members") or []):
            return p
    return None

def manager_of(bundle, member_id):
    """Pod manager for a worker, or None if the member is not in a pod / is the manager."""
    p = pod_of(bundle, member_id)
    if not p: return None
    mgr = p.get("manager")
    return mgr if mgr and mgr != member_id else None

def escalate_target(bundle, member_id):
    """Who a member should ask when stuck: pod manager if any, else first can_talk_to."""
    mgr = manager_of(bundle, member_id)
    if mgr: return mgr
    agent = member_agent(bundle, member_id) or {}
    talks = agent.get("can_talk_to") or []
    return talks[0] if talks else None

def build_messages(bundle, run_dir, member_id, task_text, *, extra_files=None, proj=None):
    """Return OpenAI-style messages for one invocation. Memory policy controls history."""
    agent = member_agent(bundle, member_id)
    if not agent: raise KeyError(member_id)
    mem = agent.get("memory") or "task"
    system = (agent.get("instructions") or agent.get("goal") or "").strip()
    system += f"\n\nYou are member `{member_id}` ({agent.get('name') or agent.get('role') or 'agent'})."
    system += "\nReply with a single JSON object matching the action schema. Only one action per turn."
    if agent.get("can_talk_to"):
        system += f"\nYou may message: {', '.join(agent['can_talk_to'])}."
    mgr = manager_of(bundle, member_id)
    if mgr:
        system += f"\nYour pod manager is `{mgr}`. Use action=escalate (or ask) to reach them when stuck."
    msgs = [{"role": "system", "content": system}]
    body = [f"Goal: {agent.get('goal') or ''}", f"Task:\n{task_text}"]
    if extra_files:
        for name, text in extra_files.items():
            body.append(f"\n### file {name}\n{text[:12000]}")
    # recent inbox (always: current task needs unanswered questions)
    inbox = mailbox.inbox(run_dir, member_id)[-12:]
    if mem == "none":
        inbox = [m for m in inbox if m.get("type") in ("TASK", "QUESTION") and m.get("state") != "read"][-4:]
    elif mem == "invocation":
        inbox = inbox[-2:]
    elif mem in ("task", "project", "persistent"):
        pass
    if inbox:
        lines = []
        for m in inbox:
            lines.append(f"- [{m.get('type')}] from {m.get('from')} id={m.get('id')}: {json_preview(m.get('payload'))}")
        body.append("Inbox:\n" + "\n".join(lines))
    arts = artifacts.list_artifacts(run_dir)[-8:]
    if arts and mem != "none":
        body.append("Artifacts: " + ", ".join(f"{a['id']}({a['kind']})" for a in arts))
    if mem in ("project", "persistent") and proj is not None:
        try:
            from codebot import memory
            lessons = memory.select(proj, {"title": (task_text or "")[:200], "files": []}, k=3, cap=600)
            if lessons:
                body.append("Lessons:\n" + "\n".join(f"- {L.get('text') or L}" for L in lessons))
        except Exception:
            pass
    msgs.append({"role": "user", "content": "\n\n".join(body)})
    return msgs, agent

def json_preview(payload, n=500):
    if isinstance(payload, str): return payload[:n]
    import json
    try: return json.dumps(payload, ensure_ascii=False)[:n]
    except TypeError: return str(payload)[:n]
