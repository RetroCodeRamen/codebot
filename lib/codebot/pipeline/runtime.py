"""Durable pipeline run state under projects/<name>/pipeline/."""
import json, os, time, copy
from .load import load_project
from .validate import ValidationError

RUN_FILE = "run.json"

def pipeline_dir(proj): return proj.p("pipeline")

def run_path(proj): return os.path.join(pipeline_dir(proj), RUN_FILE)

def workspace_of(proj, bundle=None):
    b = bundle or {}
    ws = ((b.get("project") or {}).get("workspace") or {}).get("root")
    if ws and os.path.isabs(ws): return ws
    if ws: return os.path.join(proj.rundir or proj.workdir or proj.state, ws)
    return proj.rundir or proj.workdir or proj.state

def empty_run(bundle, *, pipeline_ref=None):
    members = {m["id"]: {"state": "SUSPENDED", "node": None, "wait_for": None, "steps": 0}
               for m in (bundle.get("team") or {}).get("members") or []}
    nodes = {n["id"]: n for n in (bundle.get("workflow") or {}).get("nodes") or []}
    start = next(n["id"] for n in (bundle.get("workflow") or {}).get("nodes") or [] if n.get("type") == "start")
    return {
        "schema": "codebot.pipeline/v1",
        "status": "READY",
        "pipeline_ref": pipeline_ref,
        "project_id": (bundle.get("project") or {}).get("id"),
        "workflow_id": (bundle.get("workflow") or {}).get("id"),
        "team_id": (bundle.get("team") or {}).get("id"),
        "cursor": start,
        "active": None,
        "queue": [],              # fan-out member/node pairs waiting for the single inference slot
        "join_pending": {},       # join_node_id -> set of unfinished branch node ids (stored as list)
        "loop_counts": {},
        "completed_nodes": [],   # run-node ids finished since last join (for join readiness)
        "members": members,
        "nodes": {nid: {"visits": 0} for nid in nodes},
        "steps_done": [],
        "step_count": 0,
        "max_steps": int(((bundle.get("project") or {}).get("limits") or {}).get("max_steps") or 200),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "error": None,
        "fit": ((bundle.get("project") or {}).get("generated") or {}).get("fit"),      # the Team Designer's size estimate, to hold against fit_actual
        "used_s": 0.0,
        "last": None
    }

def save_run(proj, run):
    d = pipeline_dir(proj); os.makedirs(d, exist_ok=True)
    run = dict(run); run["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = run_path(proj) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(run, f, indent=1, ensure_ascii=False)
    os.replace(tmp, run_path(proj))
    return run

def load_run(proj):
    p = run_path(proj)
    if not os.path.isfile(p): return None
    with open(p, encoding="utf-8") as f: return json.load(f)

def load_bundle(proj):
    """Load the project's pipeline JSON (projects/<name>/pipeline/project.json or PIPELINE= path)."""
    ref = proj.get("PIPELINE")
    candidates = []
    if ref:
        if os.path.isabs(ref): candidates.append(ref)
        else:
            candidates.append(os.path.join(proj.state, ref))                 # PIPELINE=pipeline/project.json
            candidates.append(os.path.join(pipeline_dir(proj), os.path.basename(ref)))
            candidates.append(os.path.join(proj.home, ref))
    candidates.append(os.path.join(pipeline_dir(proj), "project.json"))
    for path in candidates:
        if path and os.path.isfile(path): return load_project(path), path
    raise ValidationError([f"no pipeline project.json for {proj.name} (set PIPELINE= or write pipeline/project.json)"])

def ensure_run(proj, bundle=None, pipeline_ref=None):
    run = load_run(proj)
    if run: return run
    if bundle is None: bundle, pipeline_ref = load_bundle(proj)
    return save_run(proj, empty_run(bundle, pipeline_ref=pipeline_ref))

def node_map(bundle):
    return {n["id"]: n for n in (bundle.get("workflow") or {}).get("nodes") or []}

def edges_from(bundle, node_id, when=None):
    out = []
    for e in (bundle.get("workflow") or {}).get("edges") or []:
        if e.get("from") != node_id: continue
        if when is not None and e.get("when") not in (None, when): continue
        out.append(e)
    return out
