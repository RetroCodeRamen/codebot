"""Deterministic validators for pipeline JSON. No JSON Schema library: hand checks, clear errors."""
import re
from . import schema as S

SCHEMA = S.SCHEMA
AGENT_TOOLS = ("read", "list", "check", "run")                      # what each one does: tools.py
GATE_CHECKS = ("examples", "tests", "spec-examples", "qa", "approve")      # what each one runs: gates.py
_ID = re.compile(S.ID)
_PATH = re.compile(S.PATH_GLOB)

class ValidationError(Exception):
    """One or more problems; `.errors` is a list of human-readable strings."""
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) if self.errors else "invalid pipeline config")

def _need(obj, *keys):
    return [f"missing {k}" for k in keys if k not in obj or obj[k] in (None, "")]

def _id(val, where):
    if not isinstance(val, str) or not _ID.match(val): return [f"{where}: bad id {val!r} (need {S.ID})"]
    return []

def _paths(lst, where):
    err = []
    if lst is None: return err
    if not isinstance(lst, list): return [f"{where}: must be a list of path globs"]
    for p in lst:
        if not isinstance(p, str) or not _PATH.match(p) or ".." in p.split("/"):
            err.append(f"{where}: unsafe or invalid path {p!r}")
    return err

def _write_allowed(write, reads):
    """True when a write glob is identical to a read entry or sits under a read `dir/**` / `*` / `**`."""
    if write in reads or "*" in reads or "**" in reads: return True
    for r in reads:
        if r.endswith("/**"):
            root = r[:-3]
            if write == root.rstrip("/") or write.startswith(root) or write.startswith(root.rstrip("/") + "/"): return True
        if r == write: return True
    return False

def validate_agent(doc, where="agent"):
    err = []
    if not isinstance(doc, dict): return [f"{where}: not an object"]
    if doc.get("schema") != S.SCHEMA: err.append(f"{where}: schema must be {S.SCHEMA!r}")
    if doc.get("kind") != "agent": err.append(f"{where}: kind must be 'agent'")
    err += _need(doc, "id", "name", "goal")
    err += _id(doc.get("id", ""), f"{where}.id")
    mem = doc.get("memory", "task")
    if mem not in S.MEMORY: err.append(f"{where}.memory: want one of {S.MEMORY}, got {mem!r}")
    sees = doc.get("sees") if isinstance(doc.get("sees"), dict) else {}
    if "sees" in doc and not isinstance(doc.get("sees"), dict):
        err.append(f"{where}.sees: must be an object with read/write lists")
    else:
        err += _paths(sees.get("read"), f"{where}.sees.read")
        err += _paths(sees.get("write"), f"{where}.sees.write")
        reads = list(sees.get("read") or [])
        for w in sees.get("write") or []:
            if not _write_allowed(w, reads):
                err.append(f"{where}: write path {w!r} is not under sees.read")
    talk = doc.get("can_talk_to") or []
    if talk is not None and not isinstance(talk, list): err.append(f"{where}.can_talk_to: must be a list")
    else:
        for t in talk or []: err += _id(t, f"{where}.can_talk_to")
    msgs = doc.get("messages") or []
    if msgs and not isinstance(msgs, list): err.append(f"{where}.messages: must be a list")
    else:
        for m in msgs or []:
            if m not in S.MESSAGE: err.append(f"{where}.messages: unknown type {m!r}")
    if "limits" in doc and not isinstance(doc["limits"], dict): err.append(f"{where}.limits: must be an object")
    if doc.get("tools") is not None:
        if not isinstance(doc["tools"], list): err.append(f"{where}.tools: must be a list")
        else: err += [f"{where}.tools: unknown tool {t!r} (known: {', '.join(AGENT_TOOLS)})" for t in doc["tools"] if t not in AGENT_TOOLS]
    if "model" in doc and doc["model"] is not None and not isinstance(doc["model"], (str, dict)):
        err.append(f"{where}.model: string or object")
    return err

def validate_team(doc, agents=None, where="team"):
    err = []
    if not isinstance(doc, dict): return [f"{where}: not an object"]
    if doc.get("schema") != S.SCHEMA: err.append(f"{where}: schema must be {S.SCHEMA!r}")
    if doc.get("kind") != "team": err.append(f"{where}: kind must be 'team'")
    err += _need(doc, "id", "name")
    err += _id(doc.get("id", ""), f"{where}.id")
    members = doc.get("members") or []
    if not isinstance(members, list) or not members: err.append(f"{where}: members must be a non-empty list"); return err
    ids, templates = set(), set(agents or [])
    for i, m in enumerate(members):
        w = f"{where}.members[{i}]"
        if not isinstance(m, dict): err.append(f"{w}: not an object"); continue
        err += _need(m, "id", "agent")
        err += _id(m.get("id", ""), f"{w}.id")
        err += _id(m.get("agent", ""), f"{w}.agent")
        if m.get("id") in ids: err.append(f"{w}: duplicate member id {m.get('id')!r}")
        ids.add(m.get("id"))
        if templates and m.get("agent") not in templates: err.append(f"{w}: unknown agent template {m.get('agent')!r}")
        for link in m.get("links") or []:
            if not isinstance(link, dict): err.append(f"{w}.links: bad entry"); continue
            kind = link.get("kind")
            if kind not in S.LINK: err.append(f"{w}.links: kind must be one of {S.LINK}")
            if "to" in link: err += _id(link["to"], f"{w}.links.to")
    # communication targets must name members (or be deferred until project merge)
    member_ids = {m.get("id") for m in members if isinstance(m, dict)}
    for i, m in enumerate(members):
        if not isinstance(m, dict): continue
        for t in m.get("can_talk_to") or []:
            if t not in member_ids: err.append(f"{where}.members[{i}]: can_talk_to {t!r} is not a member of this team")
        for link in m.get("links") or []:
            if isinstance(link, dict) and link.get("to") and link["to"] not in member_ids and link.get("kind") in ("agent", "both"):
                err.append(f"{where}.members[{i}]: link to {link['to']!r} is not a team member")
    pods = doc.get("pods") or []
    if pods and not isinstance(pods, list): err.append(f"{where}.pods: must be a list")
    else:
        for i, p in enumerate(pods or []):
            w = f"{where}.pods[{i}]"
            if not isinstance(p, dict): err.append(f"{w}: not an object"); continue
            err += _id(p.get("id", ""), f"{w}.id")
            for mid in p.get("members") or []:
                if mid not in member_ids: err.append(f"{w}: member {mid!r} not in team")
            mgr = p.get("manager")
            if mgr and mgr not in member_ids: err.append(f"{w}: manager {mgr!r} not in team")
    return err

def validate_workflow(doc, member_ids=None, where="workflow"):
    err = []
    if not isinstance(doc, dict): return [f"{where}: not an object"]
    if doc.get("schema") != S.SCHEMA: err.append(f"{where}: schema must be {S.SCHEMA!r}")
    if doc.get("kind") != "workflow": err.append(f"{where}: kind must be 'workflow'")
    err += _need(doc, "id", "name", "nodes", "edges")
    err += _id(doc.get("id", ""), f"{where}.id")
    nodes = doc.get("nodes") or []
    edges = doc.get("edges") or []
    if not isinstance(nodes, list) or not nodes: err.append(f"{where}: nodes must be a non-empty list"); return err
    if not isinstance(edges, list): err.append(f"{where}: edges must be a list"); return err
    nids, starts, ends = set(), [], []
    for i, n in enumerate(nodes):
        w = f"{where}.nodes[{i}]"
        if not isinstance(n, dict): err.append(f"{w}: not an object"); continue
        err += _need(n, "id", "type")
        err += _id(n.get("id", ""), f"{w}.id")
        if n.get("id") in nids: err.append(f"{w}: duplicate node id")
        nids.add(n.get("id"))
        if n.get("type") not in S.NODE: err.append(f"{w}: type must be one of {S.NODE}")
        if n.get("type") == "start": starts.append(n.get("id"))
        if n.get("type") == "end": ends.append(n.get("id"))
        if n.get("type") == "run":
            if not n.get("agent"): err.append(f"{w}: run node needs agent (member id)")
            elif member_ids is not None and n["agent"] not in member_ids:
                err.append(f"{w}: agent {n['agent']!r} is not a team member")
        if n.get("type") == "loop":
            lim = (n.get("limits") or {}).get("max_iterations") if isinstance(n.get("limits"), dict) else n.get("max_iterations")
            if lim is None: err.append(f"{w}: loop needs max_iterations")
            elif not isinstance(lim, int) or lim < 1: err.append(f"{w}: max_iterations must be a positive int")
        if n.get("type") == "gate" and n.get("check") not in GATE_CHECKS:
            err.append(f"{w}: gate needs check, one of {'|'.join(GATE_CHECKS)} (got {n.get('check')!r})")
    if len(starts) != 1: err.append(f"{where}: need exactly one start node, found {len(starts)}")
    if not ends: err.append(f"{where}: need at least one end node")
    for i, e in enumerate(edges):
        w = f"{where}.edges[{i}]"
        if not isinstance(e, dict): err.append(f"{w}: not an object"); continue
        err += _need(e, "from", "to")
        if e.get("from") not in nids: err.append(f"{w}: from {e.get('from')!r} unknown")
        if e.get("to") not in nids: err.append(f"{w}: to {e.get('to')!r} unknown")
        kind = e.get("kind")
        if kind and kind not in S.LINK and kind not in ("execution", "artifact", "handoff"):
            err.append(f"{w}: kind must be agent|repository|both|execution|artifact|handoff")
    # reachability from start
    if starts and nids:
        seen, stack = set(), [starts[0]]
        adj = {n: [] for n in nids}
        for e in edges:
            if isinstance(e, dict) and e.get("from") in adj and e.get("to") in nids:
                adj[e["from"]].append(e["to"])
        while stack:
            u = stack.pop()
            if u in seen: continue
            seen.add(u)
            stack.extend(adj.get(u) or [])
        unreachable = sorted(nids - seen)
        if unreachable: err.append(f"{where}: unreachable nodes from start: {', '.join(unreachable)}")
    # unbounded wait cycles: every loop node already required max_iterations; detect pure cycles with no gate/loop bound
    return err

def validate_project(doc, where="project"):
    """Validate a fully resolved project bundle: {project, agents{}, team, workflow} or a single project doc with embeds."""
    err = []
    if not isinstance(doc, dict): raise ValidationError([f"{where}: not an object"])
    # Accept either a bare project doc or a bundle produced by load_project.
    if doc.get("kind") == "project" or "project" not in doc:
        bundle = _as_bundle(doc)
    else:
        bundle = doc
    proj = bundle.get("project") or {}
    agents = bundle.get("agents") or {}
    team = bundle.get("team")
    workflow = bundle.get("workflow")
    if proj.get("schema") != S.SCHEMA: err.append(f"{where}: schema must be {S.SCHEMA!r}")
    if proj.get("kind") not in (None, "project") and "project" in bundle:
        if proj.get("kind") != "project": err.append(f"{where}: kind must be 'project'")
    if not proj.get("id"): err.append(f"{where}: missing id")
    else: err += _id(proj["id"], f"{where}.id")
    if not isinstance(agents, dict) or not agents: err.append(f"{where}: agents map is empty")
    for aid, a in (agents or {}).items():
        err += validate_agent(a, f"agents.{aid}")
        if a.get("id") and a["id"] != aid: err.append(f"agents.{aid}: id field {a.get('id')!r} != key")
    if not team: err.append(f"{where}: missing team")
    else: err += validate_team(team, agents=set(agents), where="team")
    member_ids = {m["id"] for m in (team or {}).get("members") or [] if isinstance(m, dict) and "id" in m}
    if not workflow: err.append(f"{where}: missing workflow")
    else: err += validate_workflow(workflow, member_ids=member_ids, where="workflow")
    # every run node's agent template must exist
    if team and agents:
        for m in team.get("members") or []:
            if isinstance(m, dict) and m.get("agent") and m["agent"] not in agents:
                err.append(f"team member {m.get('id')}: agent template {m['agent']!r} missing")
    if err: raise ValidationError(err)
    return bundle

def _as_bundle(doc):
    """Normalize embedded or flat project shapes into a bundle."""
    if "project" in doc and isinstance(doc["project"], dict):
        return {"project": doc["project"], "agents": doc.get("agents") or {}, "team": doc.get("team"), "workflow": doc.get("workflow"),
                "ui": doc.get("ui")}
    # single file with kind=project that inlines refs as objects
    agents = doc.get("agents") or {}
    if isinstance(agents, list):
        agents = {a["id"]: a for a in agents if isinstance(a, dict) and a.get("id")}
    return {"project": {k: v for k, v in doc.items() if k not in ("agents", "team", "workflow", "ui")},
            "agents": agents, "team": doc.get("team"), "workflow": doc.get("workflow"), "ui": doc.get("ui")}

def validate_doc(doc):
    """Validate one standalone document by its kind."""
    if not isinstance(doc, dict): raise ValidationError(["not an object"])
    kind = doc.get("kind")
    if kind == "agent":
        err = validate_agent(doc)
    elif kind == "team":
        err = validate_team(doc)
    elif kind == "workflow":
        err = validate_workflow(doc)
    elif kind == "project":
        return validate_project(doc)
    else:
        raise ValidationError([f"unknown kind {kind!r}"])
    if err: raise ValidationError(err)
    return doc
