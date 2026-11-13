"""The Team Designer's proposal -> team and workflow JSON.

The rule that sizes a team (the user's, 2026-11-12): a project that fits completely in one agent's context window is built best by one agent;
a team begins to help only when the context would have to keep shrinking to hold everything. And there is always a plan, a build and a review:

  fits one context     plan -> build -> review -> fix                      three agents: planner, builder, reviewer
  does not fit         plan -> manager (interfaces) -> each team builds, is reviewed and fixes its part -> manager checks how the parts connect
                       and sends problems back -> each team fixes -> manager's review -> QA writes tests from the expected behaviour ->
                       failures go to the manager, who fixes or delegates -> the spec's examples decide

The model answers one constrained question (TEAM_SCHEMA): the parts of the program, the files of each, how many lines each will take and which
parts build on which. The harness decides the shape from the estimate (`fit`) and writes the agents, the team and the workflow, so what runs
is always valid: ids are repaired, a file has one owner, a part without files is dropped. Nothing here calls a model.
"""
import re
from codebot import drafts
from .schema import SCHEMA
from .validate import validate_project

MAX_WORKERS = 6
RESERVED = ("planner", "builder", "reviewer", "manager", "qa")
QA_FILE = "qa_tests.py"
TOKENS_PER_LINE = 11          # a line of source, as tokens (Python and Go files of the recorded runs: 9 to 12)
LINES_PER_REQUIREMENT = 25    # the floor for a model's estimate: models guess low
_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,120}$")

TEAM_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "workers"],
    "properties": {
        "summary": {"type": "string"},
        "workers": {"type": "array", "minItems": 1, "maxItems": MAX_WORKERS, "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "responsibility", "files", "lines"],
            "properties": {"id": {"type": "string"}, "specialty": {"type": "string"}, "responsibility": {"type": "string"},
                           "files": {"type": "array", "items": {"type": "string"}},
                           "lines": {"type": "integer"},
                           "needs": {"type": "array", "items": {"type": "string"}}}}}
    }
}

SYSTEM = (
    "You outline how a program will be built from its SPEC. Split it into parts that can be written separately: usually one part per source "
    "file or per clearly separate component, and ONE part when the program is a single file. For each part give the files it writes (a file "
    "belongs to exactly one part), an honest estimate of how many lines of code those files will need in total (`lines`), and `needs`: the "
    "parts whose files it builds on. Part ids are short lowercase words. Do not decide the team: the harness sizes it from your estimate.")

def prompt(spec, proj=None):
    names = drafts.spec_files(spec or ""); cov = drafts.coverage(spec or "")
    facts = [f"Source files the SPEC names: {', '.join(names) if names else '(none: the program is one file)'}.",
             f"Requirements: {len(cov['requirements'])}; worked examples the harness can run: {cov['examples']}."]
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "SPEC:\n" + (spec or "")[:12000] + "\n\n" + "\n".join(facts)}]

def _slug(text, taken):
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:24] or "part"
    if not s[0].isalpha(): s = "p-" + s
    base, n = s, 2
    while s in taken or s in RESERVED or s.endswith("-review"): s = f"{base}-{n}"; n += 1
    return s

def _file(f):
    f = str(f or "").strip().strip("`")
    if f.startswith("./"): f = f[2:]
    return f if _FILE.match(f) and ".." not in f.split("/") and f not in ("SPEC.md", "PLAN.md", "INTERFACES.md", QA_FILE) and not f.endswith("/") else None

def clean(choice, spec):
    """(parts [{id, name, responsibility, files, lines, needs}], notes): the proposal with every piece the harness cannot use repaired or dropped."""
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
        lines = w.get("lines") if isinstance(w.get("lines"), int) and not isinstance(w.get("lines"), bool) else 0
        out.append({"id": wid, "name": str(w.get("specialty") or wid)[:60], "responsibility": str(w.get("responsibility") or "").strip()[:600],
                    "files": files, "lines": max(0, min(lines, 200000)), "needs": [str(n) for n in (w.get("needs") if isinstance(w.get("needs"), list) else [])]})
    for f in drafts.spec_files(spec or ""):                      # a file the SPEC names and nobody took goes to the last part
        if out and f not in owned: owned[f] = out[-1]["id"]; out[-1]["files"].append(f); notes.append(f"{out[-1]['id']}: given {f}, which no part had")
    for w in [w for w in out if not w["files"]]:
        notes.append(f"{w['id']}: dropped, it had no file to write")
    out = [w for w in out if w["files"]]; ids = {w["id"] for w in out}
    for w in out: w["needs"] = [rename.get(n, n) for n in w["needs"] if rename.get(n, n) in ids and rename.get(n, n) != w["id"]]
    out, cycle = _ordered(out)
    if cycle: notes.append(f"{', '.join(cycle)} need each other in a circle, which no build order satisfies: their `needs` were dropped and they are built in the order given")
    return out, notes

def _ordered(parts):
    """(parts after the ones they need, the ids caught in a dependency cycle). Where the order is free it is the given one. Parts in a cycle
    (or waiting on one) lose their `needs` and keep the given order: the caller says so."""
    done, out, left, cycle = set(), [], list(parts), []
    while left:
        ready = [w for w in left if all(n in done for n in w["needs"])]
        if not ready:
            cycle = [w["id"] for w in left]
            for w in left: w["needs"] = []
            ready = left[:1]
        w = ready[0]; out.append(w); done.add(w["id"]); left.remove(w)
    return out, cycle

def fit(spec, parts, num_ctx=None, reply_tokens=None):
    """Does the whole program fit in one agent's context window? One agent must hold the spec, the whole program as it is and the whole
    program again as its reply, and that reply must fit the output limit. {lines, tokens, budget, num_ctx, fits, why}."""
    num_ctx = int(num_ctx or 32768); reply = int(reply_tokens or 6000)
    floor = LINES_PER_REQUIREMENT * len(drafts.coverage(spec or "")["requirements"])
    lines = max(sum(p["lines"] for p in parts), floor); tokens = lines * TOKENS_PER_LINE
    spec_tokens = len(spec or "") // 3
    budget = min((num_ctx - spec_tokens - 2000) // 2, reply)
    return {"lines": lines, "tokens": tokens, "budget": budget, "num_ctx": num_ctx, "fits": tokens <= budget,
            "why": f"about {lines} lines ({tokens} tokens) against {budget} tokens one agent can write in one reply with the spec and the code in a {num_ctx}-token context"}

def _agent(aid, name, role, goal, instructions, read, write, tools, memory="task", turns=16, reply=None):
    """reply = the agent's output limit in tokens (model.num_predict). An agent that writes no source needs little: the first real reviewer twice
    ran to the 4,000-token default, about six minutes each on this machine, and its review was cut off by the time cap before it reported."""
    return {"schema": SCHEMA, "kind": "agent", "id": aid, "name": name, "role": role, "goal": goal, "instructions": instructions, "memory": memory,
            **({"model": {"num_predict": reply}} if reply else {}),
            "sees": {"read": read, "write": write}, "tools": tools, "can_talk_to": [],
            "messages": ["TASK", "QUESTION", "ANSWER", "FINDING", "RESULT", "ESCALATION"], "limits": {"max_turns": turns}}

ALL = ["read", "list", "check", "run"]
LOOK = ["read", "list", "run"]              # a reviewer has no `check`: the examples passing is not its question (in the first real run the
                                            # reviewer ran check, was told nothing was left to fix, and reported nothing)
PLANNER = ("Read SPEC.md and write PLAN.md, at most 60 lines: the files to write; for each file its functions and one line on what each must do; "
           "the rules of the SPEC that are easy to get wrong; the order to build in. Do not write code. Then finish.")
REVIEW = ("You have not seen this code before. Read SPEC.md and the code and look for behaviour the SPEC requires that the code does not have; "
          "the rules that have no example in the SPEC are the likely places. Before you report a problem, run the code and see it fail "
          "(run: python3 -c \"...\", one short command per turn). Report only problems you saw: the input, what the SPEC requires, what the code did. If you saw none, report an empty list.")
FIX = ("The findings in your inbox are from a reviewer who ran the code. For each one: if SPEC.md really requires it, change the file (write the "
       "whole file, one file per turn) and run check: the SPEC's examples must keep passing. A finding the SPEC does not support is ignored. "
       "When nothing is left to change, finish.")

def _wire(order, has_examples, fix_node, meta):
    """Edges along `order`, ending in the examples gate (fail -> fix_node) or, without runnable examples, in an approval."""
    edges = [{"from": a, "to": b, "kind": "execution"} for a, b in zip(order, order[1:])]
    if has_examples:
        edges[-1]["when"] = "pass"; edges.append({"from": "gate", "to": fix_node, "kind": "execution", "when": "fail"})
    else: meta["notes"].append("SPEC.md has no example the harness can run, so nothing checks the result but your approval")
    return edges

def _final(has_examples):
    return ({"id": "gate", "type": "gate", "check": "examples", "max_retries": 2} if has_examples else
            {"id": "gate", "type": "approve", "description": "SPEC.md has no example the harness can run: a person decides"})

def small_team(spec, meta, name="designed", review=True):
    """plan -> build -> review -> fix: a planner, a builder (the drafts loop builds; the same member fixes) and a reviewer with a fresh context.
    review=False stops after the build (plan -> build): a comparison arm, not something the designer chooses."""
    has_examples = drafts.coverage(spec or "")["examples"] > 0
    agents = {
        "planner": _agent("planner", "Planner", "plan", "Plan how the program in SPEC.md will be built.", PLANNER, ["**"], ["PLAN.md"], ["read", "list"], memory="none", turns=6, reply=1500),
        "builder": _agent("builder", "Builder", "implement", "Write the whole program so that it does everything SPEC.md says.", FIX, ["**"], ["**"], ALL, turns=14),
        "reviewer": _agent("reviewer", "Reviewer", "review", "Find behaviour SPEC.md requires that the code does not have.", REVIEW, ["**"], [], LOOK, memory="none", turns=12, reply=900)}
    members = [{"id": "planner", "agent": "planner", "can_talk_to": []}, {"id": "builder", "agent": "builder", "can_talk_to": []},
               {"id": "reviewer", "agent": "reviewer", "can_talk_to": ["builder"]}]
    nodes = [{"id": "start", "type": "start"}, {"id": "plan", "type": "run", "agent": "planner", "action": "write_plan", "done_after_write": "PLAN.md"},
             {"id": "build", "type": "run", "agent": "builder", "action": "drafts"}, {"id": "review", "type": "run", "agent": "reviewer", "action": "review"},
             {"id": "fix", "type": "run", "agent": "builder", "action": "fix"}, _final(has_examples), {"id": "done", "type": "end"}]
    edges = _wire(["start", "plan", "build", "review", "fix", "gate", "done"], has_examples, "fix", meta)
    meta["template"] = "designed"; meta["shape"] = "plan-build-review-fix"
    if not review:
        agents.pop("reviewer"); members = [x for x in members if x["id"] != "reviewer"]; nodes = [n for n in nodes if n["id"] not in ("review", "fix")]
        if has_examples: nodes[-2] = {"id": "gate", "type": "gate", "check": "examples", "max_retries": 0}
        edges = _wire(["start", "plan", "build", "gate", "done"], False, None, {"notes": []})
        if has_examples: edges[-1]["when"] = "pass"
        meta["shape"] = "plan-build"
    return _bundle(name, agents, members, None, nodes, edges, 120)

def large_team(parts, spec, meta, name="designed"):
    """A manager and one team (a builder and its own reviewer) per part, an integration pass, and QA."""
    has_examples = drafts.coverage(spec or "")["examples"] > 0; ids = [p["id"] for p in parts]
    agents = {"planner": _agent("planner", "Planner", "plan", "Plan how the program in SPEC.md will be built.", PLANNER, ["**"], ["PLAN.md"], ["read", "list"], memory="none", turns=6, reply=1500)}
    agents["manager"] = _agent(
        "manager", "Manager", "coordinate", "Make the parts fit together into the program SPEC.md describes.",
        "Your work changes with the step. contracts: write INTERFACES.md from SPEC.md and PLAN.md: for every file, exactly what it provides (names, "
        "arguments, what it returns or raises) and what the other files may rely on; then finish. integrate: read every part and run check; where "
        "one part calls another, compare what is sent with what is expected; report each mismatch as a finding that names the file to change, or "
        "report an empty list. review and qa-fix: read the failures you are shown; a small fix you make yourself (write the whole file, then check), "
        "otherwise finish. The SPEC's examples must keep passing.",
        ["**"], ["**"], ALL, memory="project", turns=16)
    agents["qa"] = _agent(
        "qa", "QA", "test", "Test the finished program against what SPEC.md says it must do.",
        f"Write {QA_FILE}: a plain Python script that tests the behaviour SPEC.md requires, above all the rules its examples do not show. For each "
        "test print what is checked; when a result is wrong print the input, what was expected and what came back, and at the end exit with status 1 "
        "if any test failed (sys.exit(1)), else 0. Take every expected value from SPEC.md, never from what the code does now. Run it once (run: "
        f"python3 {QA_FILE}) to see that it runs, then finish. Do not change any other file.",
        ["**"], [QA_FILE], LOOK, memory="none", turns=8, reply=3000)
    members = [{"id": "planner", "agent": "planner", "can_talk_to": []}, {"id": "manager", "agent": "manager", "can_talk_to": list(ids)}]
    nodes = [{"id": "start", "type": "start"}, {"id": "plan", "type": "run", "agent": "planner", "action": "write_plan", "done_after_write": "PLAN.md"},
             {"id": "contracts", "type": "run", "agent": "manager", "action": "contracts", "done_after_write": "INTERFACES.md"}]
    order = ["start", "plan", "contracts"]
    for p in parts:
        others = [f"{o['id']} writes {', '.join(o['files'])}" for o in parts if o["id"] != p["id"]]
        agents["w-" + p["id"]] = _agent(
            "w-" + p["id"], p["name"], "implement", p["responsibility"] or f"Write {', '.join(p['files'])} as SPEC.md asks.",
            f"You write only: {', '.join(p['files'])}. " + ("; ".join(others) + ". Read their files when you need them; never rewrite them. " if others else "")
            + "Follow PLAN.md and INTERFACES.md. build: write each of your files whole, run check, then finish. fix: " + FIX,
            ["**"], list(p["files"]), ALL)
        agents["r-" + p["id"]] = _agent("r-" + p["id"], f"Reviewer of {p['id']}", "review", f"Find what SPEC.md requires of {', '.join(p['files'])} that the code does not do.",
                                        f"Review only: {', '.join(p['files'])}. " + REVIEW, ["**"], [], LOOK, memory="none", turns=10, reply=900)
        members += [{"id": p["id"], "agent": "w-" + p["id"], "can_talk_to": ["manager"]}, {"id": p["id"] + "-review", "agent": "r-" + p["id"], "can_talk_to": [p["id"]]}]
        for step, who in (("build", p["id"]), ("review", p["id"] + "-review"), ("fix", p["id"])):
            nodes.append({"id": f"{step}-{p['id']}", "type": "run", "agent": who, "action": step}); order.append(f"{step}-{p['id']}")
    nodes.append({"id": "integrate", "type": "run", "agent": "manager", "action": "integrate"}); order.append("integrate")
    for p in parts:                                         # each team takes back what the manager found in its part
        nodes.append({"id": f"refix-{p['id']}", "type": "run", "agent": p["id"], "action": "fix"}); order.append(f"refix-{p['id']}")
    members.append({"id": "qa", "agent": "qa", "can_talk_to": ["manager"]})
    nodes += [{"id": "manager-review", "type": "run", "agent": "manager", "action": "review"}, {"id": "qa-write", "type": "run", "agent": "qa", "action": "write_tests"},
              {"id": "qa-gate", "type": "gate", "check": "qa", "max_retries": 2, "on_exhausted": "pass"},
              {"id": "qa-fix", "type": "run", "agent": "manager", "action": "qa-fix"}, _final(has_examples), {"id": "done", "type": "end"}]
    order += ["manager-review", "qa-write", "qa-gate", "gate", "done"]
    edges = _wire(order, has_examples, "qa-fix", meta)
    for e in edges:
        if e["from"] == "qa-gate": e["when"] = "pass"
    edges += [{"from": "qa-gate", "to": "qa-fix", "kind": "execution", "when": "fail"}, {"from": "qa-fix", "to": "qa-gate", "kind": "execution"}]
    meta["template"] = "designed"; meta["shape"] = "manager-teams-qa"
    pods = [{"id": "team-" + p["id"], "manager": "manager", "members": [p["id"], p["id"] + "-review"]} for p in parts]
    return _bundle(name, agents, members, pods, nodes, edges, 120 + 60 * len(parts), reserved={QA_FILE: "qa"})

def _bundle(name, agents, members, pods, nodes, edges, max_steps, reserved=None):
    team = {"schema": SCHEMA, "kind": "team", "id": "designed", "name": "Designed team", "members": members}
    if pods: team["pods"] = pods
    project = {"schema": SCHEMA, "kind": "project", "id": "designed", "name": name, "workspace": {"root": "."}, "limits": {"max_steps": max_steps}}
    if reserved: project["reserved"] = reserved            # files only one member may write (the QA tests)
    bundle = {"project": project, "agents": agents, "team": team,
              "workflow": {"schema": SCHEMA, "kind": "workflow", "id": "designed", "name": "Designed build", "nodes": nodes, "edges": edges}, "ui": None}
    validate_project(bundle)
    return bundle

def compile_team(choice, spec, name="designed", num_ctx=None, reply_tokens=None, shape=None):
    """(bundle, meta). shape forces "small", "large" or "plan" (PIPELINE_SHAPE; "plan" is plan -> build with no review, for comparisons);
    otherwise the estimate decides. A program with fewer than two usable parts is always the small team: there is nothing to hand to separate
    teams. meta["fit"] is the prediction; a finished run's `fit_actual` is what it turned out to be."""
    parts, notes = clean(choice, spec)
    meta = {"summary": str((choice or {}).get("summary") or "")[:400], "notes": notes, "workers": [p["id"] for p in parts]}
    meta["fit"] = f = fit(spec, parts, num_ctx, reply_tokens)
    large = (shape == "large" or (shape not in ("small", "plan") and not f["fits"])) and len(parts) >= 2
    if not large and not f["fits"] and shape not in ("small", "plan"):
        notes.append("the program is estimated not to fit one context, but it was not split into two or more parts: built as one")
    meta["reason"] = ("forced " + shape + ": " if shape else "") + f["why"]
    if large: return large_team(parts, spec, meta, name), meta
    return small_team(spec, meta, name, review=shape != "plan"), meta
