"""Team Designer: propose a team+workflow JSON from a SPEC (validated before return)."""
import json, os, copy
from .load import load_project, bundle_root
from .validate import validate_project, ValidationError
from . import invoke

TEMPLATES = {
    "solo": "projects/basic-calculator.json",
    "architect-reviewers": "projects/spec-with-reviewers.json",
    "debate": "projects/spec-debate.json",
    "small-team": "projects/small-pods.json",
}

DESIGN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "template"],
    "properties": {
        "summary": {"type": "string"},
        "template": {"type": "string", "enum": ["solo", "architect-reviewers", "debate", "small-team"]},
        "notes": {"type": "string"}
    }
}

def templates_dir(home):
    return os.path.join(home, "pipelines", "templates")

def propose(home, spec_text, *, chat_fn=None, proj=None):
    """Return (bundle, meta) for a validated team. Uses a small structured choice, not free-form org charts."""
    messages = [
        {"role": "system", "content":
         "You design the smallest AI team that can implement the SPEC. "
         "Pick template 'solo' for a small clear program with examples; "
         "'architect-reviewers' when the SPEC needs independent review; "
         "'debate' when reviewers should be able to question each other; "
         "'small-team' for coordinated math/UI-style pods. Prefer solo when unsure."},
        {"role": "user", "content": "SPEC:\n" + (spec_text or "")[:12000]}
    ]
    if chat_fn is None and proj is None:
        # no model: heuristic
        choice = {"summary": "heuristic", "template": "solo" if len(spec_text or "") < 4000 else "architect-reviewers"}
    else:
        out = invoke.call_model(proj, messages, DESIGN_SCHEMA, temperature=0.1, num_predict=400, chat_fn=chat_fn)
        choice = json.loads(out.get("content") or "{}")
    name = choice.get("template") or "solo"
    if name not in TEMPLATES: name = "solo"
    path = os.path.join(templates_dir(home), TEMPLATES[name])
    bundle = load_project(path)
    meta = {"template": name, "summary": choice.get("summary") or "", "notes": choice.get("notes") or "", "path": path}
    validate_project(bundle)
    return bundle, meta

def write_project_pipeline(proj, bundle, *, template_meta=None):
    """Materialize a bundle into projects/<name>/pipeline/ for execution."""
    d = proj.p("pipeline"); os.makedirs(d, exist_ok=True)
    # write a single resolved project.json (no $ref) so runs are self-contained
    doc = {
        "schema": "codebot.pipeline/v1",
        "kind": "project",
        "id": (bundle.get("project") or {}).get("id") or proj.name,
        "name": (bundle.get("project") or {}).get("name") or proj.name,
        "agents": bundle.get("agents") or {},
        "team": bundle.get("team"),
        "workflow": bundle.get("workflow"),
        "workspace": (bundle.get("project") or {}).get("workspace") or {"root": "."},
        "limits": (bundle.get("project") or {}).get("limits") or {},
        "ui": bundle.get("ui"),
        "generated": template_meta or {}
    }
    validate_project({"project": {k: v for k, v in doc.items() if k not in ("agents", "team", "workflow", "ui", "generated")},
                      "agents": doc["agents"], "team": doc["team"], "workflow": doc["workflow"]})
    path = os.path.join(d, "project.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(doc, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    # mark project env
    env_path = proj.p("project.env")
    lines = []
    if os.path.isfile(env_path):
        with open(env_path) as f: lines = f.read().splitlines()
    keys = {"WORKFLOW": "pipeline", "PIPELINE": "pipeline/project.json"}
    seen = set(); out = []
    for ln in lines:
        if "=" in ln and not ln.strip().startswith("#"):
            k = ln.split("=", 1)[0].strip()
            if k in keys: out.append(f'{k}="{keys[k]}"'); seen.add(k); continue
        out.append(ln)
    for k, v in keys.items():
        if k not in seen: out.append(f'{k}="{v}"')
    with open(env_path + ".tmp", "w") as f: f.write("\n".join(out) + "\n")
    os.replace(env_path + ".tmp", env_path)
    return path
