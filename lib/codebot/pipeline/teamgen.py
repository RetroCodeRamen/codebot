"""The Team Designer's proposal -> team and workflow JSON.

The model answers a small constrained question (TEAM_SCHEMA: who writes which files, in what order, with a coordinator or a QA reviewer); the
harness writes the agents, the team and the workflow from it, so what runs is always valid: bad ids are repaired, a file has one owner, a worker
without files is dropped, and every multi-worker team ends in an integrator and the examples gate. One usable worker is the solo template (the
drafts loop). Nothing here calls a model.
"""
import re
from codebot import drafts
from .schema import SCHEMA
from .validate import validate_project

MAX_WORKERS = 6
RESERVED = ("coordinator", "integrator", "qa")
_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,120}$")

TEAM_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "workers", "coordinator", "qa"],
    "properties": {
        "summary": {"type": "string"},
        "workers": {"type": "array", "minItems": 1, "maxItems": MAX_WORKERS, "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "responsibility", "files"],
            "properties": {"id": {"type": "string"}, "specialty": {"type": "string"}, "responsibility": {"type": "string"},
                           "files": {"type": "array", "items": {"type": "string"}},
                           "needs": {"type": "array", "items": {"type": "string"}}}}},
        "coordinator": {"type": "boolean"},
        "qa": {"type": "boolean"}
    }
}

SYSTEM = (
    "You plan who builds a program from its SPEC. Only one model call runs at a time on this machine, so every extra worker costs time and adds "
    "integration risk. Use ONE worker unless the SPEC names several source files or clearly separate components; then give each worker the files "
    "it writes. A file belongs to exactly one worker. `needs` lists the workers whose files a worker builds on (they run first). "
    "Set coordinator to true only when the workers must agree on interfaces the SPEC does not already fix. "
    "Set qa to true only when the SPEC has behaviour its examples do not show. Worker ids are short lowercase words.")

def prompt(spec, proj=None):
    names = drafts.spec_files(spec or ""); cov = drafts.coverage(spec or "")
    facts = [f"Source files the SPEC names: {', '.join(names) if names else '(none: the program is one file)'}.",
             f"Requirements: {len(cov['requirements'])}; worked examples the harness can run: {cov['examples']}."]
    if proj is not None:
        facts.append(f"Model for every worker: {proj.get('CODEBOT_MODEL', 'codebot-model')}, context {proj.get('NUM_CTX', '32768')} tokens, one call at a time.")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "SPEC:\n" + (spec or "")[:12000] + "\n\n" + "\n".join(facts)}]

def _slug(text, taken):
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:24] or "worker"
    if not s[0].isalpha(): s = "w-" + s
    base, n = s, 2
    while s in taken or s in RESERVED: s = f"{base}-{n}"; n += 1
    return s

def _file(f):
    f = str(f or "").strip().strip("`")
    if f.startswith("./"): f = f[2:]
    return f if _FILE.match(f) and ".." not in f.split("/") and f != "SPEC.md" and not f.endswith("/") else None

def clean(choice, spec):
    """(workers [{id, name, responsibility, files, needs}], notes): the proposal with every part the harness cannot use repaired or dropped."""
    notes, taken, owned, out, rename = [], set(), {}, [], {}
    raw = (choice or {}).get("workers")
    for w in (raw if isinstance(raw, list) else [])[:MAX_WORKERS]:
        if not isinstance(w, dict): continue
        wid = _slug(w.get("id") or w.get("specialty"), taken); taken.add(wid); rename[str(w.get("id"))] = wid
        files = []
        for f in w.get("files") if isinstance(w.get("files"), list) else []:
            g = _file(f)
            if not g: notes.append(f"{wid}: dropped the file name {f!r}"); continue
            if g in owned: notes.append(f"{wid}: {g} already belongs to {owned[g]}"); continue
            owned[g] = wid; files.append(g)
        out.append({"id": wid, "name": str(w.get("specialty") or wid)[:60], "responsibility": str(w.get("responsibility") or "").strip()[:600],
                    "files": files, "needs": [str(n) for n in (w.get("needs") if isinstance(w.get("needs"), list) else [])]})
    for f in drafts.spec_files(spec or ""):                      # a file the SPEC names and nobody took goes to the last worker
        if out and f not in owned: owned[f] = out[-1]["id"]; out[-1]["files"].append(f); notes.append(f"{out[-1]['id']}: given {f}, which no worker had")
    for w in [w for w in out if not w["files"]]:
        notes.append(f"{w['id']}: dropped, it had no file to write")
    out = [w for w in out if w["files"]]; ids = {w["id"] for w in out}
    for w in out: w["needs"] = [rename.get(n, n) for n in w["needs"] if rename.get(n, n) in ids and rename.get(n, n) != w["id"]]
    return _ordered(out), notes

def _ordered(workers):
    """Workers after the ones they need (the given order where it is free; a cycle keeps the given order)."""
    done, out, left = set(), [], list(workers)
    while left:
        ready = [w for w in left if all(n in done for n in w["needs"])] or left[:1]
        w = ready[0]; out.append(w); done.add(w["id"]); left.remove(w)
    return out

def _agent(aid, name, role, goal, instructions, read, write, tools, memory="task", turns=16):
    return {"schema": SCHEMA, "kind": "agent", "id": aid, "name": name, "role": role, "goal": goal, "instructions": instructions, "memory": memory,
            "sees": {"read": read, "write": write}, "tools": tools, "can_talk_to": [],
            "messages": ["TASK", "QUESTION", "ANSWER", "FINDING", "RESULT", "ESCALATION"], "limits": {"max_turns": turns}}

def compile_team(choice, spec, name="designed"):
    """(bundle or None, meta). None means one worker is enough (or nothing usable came back): use the solo template."""
    workers, notes = clean(choice, spec)
    meta = {"summary": str((choice or {}).get("summary") or "")[:400], "notes": notes, "workers": [w["id"] for w in workers]}
    if len(workers) < 2:
        meta["reason"] = "one worker is enough" if workers else "the proposal had no usable worker"
        return None, meta
    coord, qa = bool(choice.get("coordinator")), bool(choice.get("qa"))
    has_examples = drafts.coverage(spec or "")["examples"] > 0
    agents, members, nodes, order = {}, [], [{"id": "start", "type": "start"}], ["start"]
    ids = [w["id"] for w in workers]
    def add(mid, agent, talk, node, action):
        agents[agent["id"]] = agent; members.append({"id": mid, "agent": agent["id"], "can_talk_to": talk})
        nodes.append({"id": node, "type": "run", "agent": mid, "action": action}); order.append(node)
    if coord:
        add("coordinator", _agent("coordinator", "Coordinator", "coordinate",
                                  "Write PLAN.md: for every file, the functions or commands it provides and what the other files may rely on.",
                                  "Read SPEC.md, write PLAN.md once, then finish. Later you answer the workers' questions about the plan. Do not write source files.",
                                  ["**"], ["PLAN.md"], ["read", "list"], memory="project", turns=6), ids, "plan", "write_plan")
    for w in workers:
        others = [f"{o['id']} writes {', '.join(o['files'])}" for o in workers if o["id"] != w["id"]]
        add(w["id"], _agent("w-" + w["id"], w["name"], "implement", w["responsibility"] or f"Write {', '.join(w['files'])} as SPEC.md asks.",
                            f"You write only: {', '.join(w['files'])}. " + ("; ".join(others) + ". Read their files when you need them; never rewrite them. " if others else "")
                            + ("Follow PLAN.md. " if coord else "") + "Write each of your files whole, use check to see which of the SPEC's examples fail, then finish.",
                            ["**"], list(w["files"]), ["read", "list", "check", "run"]),
            ["coordinator"] if coord else [], "build-" + w["id"], "implement")
    if qa:
        add("qa", _agent("qa", "QA reviewer", "review", "Find behaviour SPEC.md requires that the code does not have.",
                         "Read SPEC.md and the code, run it, and report concrete findings (the input, what the SPEC requires, what happened). Do not write files.",
                         ["**"], [], ["read", "list", "check", "run"], memory="none", turns=8), ["integrator"], "review", "review")
    add("integrator", _agent("integrator", "Integrator", "integrate", "Make every worked example in SPEC.md pass with all the files together.",
                             "Run check first. Fix what fails in whichever file is wrong, one file per turn, and check again. Finish when the examples pass.",
                             ["**"], ["**"], ["read", "list", "check", "run"], turns=20), [], "integrate", "integrate")
    nodes.append({"id": "gate", "type": "gate", "check": "examples", "max_retries": 2} if has_examples else
                 {"id": "gate", "type": "approve", "description": "SPEC.md has no example the harness can run: a person decides"})
    nodes.append({"id": "done", "type": "end"}); order += ["gate", "done"]
    edges = [{"from": a, "to": b, "kind": "execution"} for a, b in zip(order, order[1:])]
    if has_examples:
        edges[-1]["when"] = "pass"; edges.append({"from": "gate", "to": "integrate", "kind": "execution", "when": "fail"})
    else: meta["notes"].append("SPEC.md has no example the harness can run, so nothing checks the result but your approval")
    team = {"schema": SCHEMA, "kind": "team", "id": "designed", "name": "Designed team", "members": members}
    if coord: team["pods"] = [{"id": "build", "manager": "coordinator", "members": ids}]
    bundle = {"project": {"schema": SCHEMA, "kind": "project", "id": "designed", "name": name, "workspace": {"root": "."},
                          "limits": {"max_steps": 60 + 30 * len(members)}},
              "agents": agents, "team": team,
              "workflow": {"schema": SCHEMA, "kind": "workflow", "id": "designed", "name": "Designed build", "nodes": nodes, "edges": edges}, "ui": None}
    validate_project(bundle)
    meta["template"] = "designed"
    return bundle, meta
