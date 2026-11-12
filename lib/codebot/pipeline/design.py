"""Team Designer: propose / init a team+workflow JSON from a SPEC (validated before return)."""
import json, os, shutil
from .load import load_project
from .validate import validate_project, ValidationError
from . import invoke

TEMPLATES = {
    "solo": "projects/basic-calculator.json",
    "architect-reviewers": "projects/spec-with-reviewers.json",
    "debate": "projects/spec-debate.json",
    "small-team": "projects/small-pods.json",
    "nested": "projects/nested-pods.json",
}

DESIGN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "template"],
    "properties": {
        "summary": {"type": "string"},
        "template": {"type": "string", "enum": ["solo", "architect-reviewers", "debate", "small-team", "nested"]},
        "notes": {"type": "string"}
    }
}

def templates_dir(home=None):
    """Prefer $CODEBOT_HOME/pipelines/templates; fall back to the repo next to lib/."""
    cands = []
    if home:
        cands.append(os.path.join(home, "pipelines", "templates"))
    pkg_root = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    cands.append(os.path.join(pkg_root, "pipelines", "templates"))
    for c in cands:
        if os.path.isdir(os.path.join(c, "projects")):
            return c
    return cands[0]

def ensure_templates(home):
    """Copy shipped templates into home if missing (so design/init work with a data-only CODEBOT_HOME)."""
    dest = os.path.join(home, "pipelines", "templates")
    if os.path.isdir(os.path.join(dest, "projects")):
        return dest
    src = templates_dir(None)
    if not os.path.isdir(os.path.join(src, "projects")):
        raise ValidationError([f"no pipeline templates at {src}"])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest

def propose(home, spec_text, *, chat_fn=None, proj=None, template=None):
    """Return (bundle, meta) for a validated team. Uses a small structured choice, not free-form org charts."""
    ensure_templates(home)
    if template:
        name = template if template in TEMPLATES else "solo"
        choice = {"summary": f"forced {name}", "template": name}
    elif chat_fn is None and proj is None:
        choice = {"summary": "heuristic", "template": "solo" if len(spec_text or "") < 4000 else "architect-reviewers"}
    else:
        messages = [
            {"role": "system", "content":
             "You design the smallest AI team that can implement the SPEC. "
             "Pick template 'solo' for a small clear program with examples; "
             "'architect-reviewers' when the SPEC needs independent review; "
             "'debate' when reviewers should be able to question each other; "
             "'small-team' for coordinated math/UI-style pods. Prefer solo when unsure."},
            {"role": "user", "content": "SPEC:\n" + (spec_text or "")[:12000]}
        ]
        out = invoke.call_model(proj, messages, DESIGN_SCHEMA, temperature=0.1, num_predict=400, chat_fn=chat_fn)
        choice = json.loads(out.get("content") or "{}")
    name = choice.get("template") or "solo"
    if name not in TEMPLATES:
        name = "solo"
    path = os.path.join(templates_dir(home), TEMPLATES[name])
    if not os.path.isfile(path):
        path = os.path.join(ensure_templates(home), TEMPLATES[name])
    bundle = load_project(path)
    meta = {"template": name, "summary": choice.get("summary") or "", "notes": choice.get("notes") or "", "path": path}
    validate_project(bundle)
    return bundle, meta

def _sync_spec(proj):
    """Ensure workspace has SPEC.md (prefer state copy, else leave workspace alone)."""
    ws = proj.rundir or proj.workdir or proj.state
    if not ws:
        return
    st = proj.p("SPEC.md")
    dst = os.path.join(ws, "SPEC.md")
    if os.path.isfile(st) and (not os.path.isfile(dst) or os.path.getmtime(st) >= os.path.getmtime(dst)):
        os.makedirs(ws, exist_ok=True)
        with open(st, encoding="utf-8") as f:
            text = f.read()
        with open(dst, "w", encoding="utf-8") as f:
            f.write(text)

def write_project_pipeline(proj, bundle, *, template_meta=None):
    """Materialize a bundle into projects/<name>/pipeline/ for execution."""
    d = proj.p("pipeline")
    os.makedirs(d, exist_ok=True)
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
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    env_path = proj.p("project.env")
    lines = []
    if os.path.isfile(env_path):
        with open(env_path) as f:
            lines = f.read().splitlines()
    keys = {"WORKFLOW": "pipeline", "PIPELINE": "pipeline/project.json"}
    seen = set()
    out = []
    for ln in lines:
        if "=" in ln and not ln.strip().startswith("#"):
            k = ln.split("=", 1)[0].strip()
            if k in keys:
                out.append(f'{k}="{keys[k]}"')
                seen.add(k)
                continue
        out.append(ln)
    for k, v in keys.items():
        if k not in seen:
            out.append(f'{k}="{v}"')
    with open(env_path + ".tmp", "w") as f:
        f.write("\n".join(out) + "\n")
    os.replace(env_path + ".tmp", env_path)
    _sync_spec(proj)
    return path

def init_project(proj, template="solo", *, chat_fn=None, force=False):
    """Install a template (or Team Designer choice) onto an existing project. Opt-in; sets WORKFLOW=pipeline."""
    if not force and os.path.isfile(proj.p("pipeline", "project.json")):
        raise ValidationError(["pipeline/project.json already exists (pass force=True / --force to replace)"])
    ensure_templates(proj.home)
    spec = ""
    for p in (proj.p("SPEC.md"), os.path.join(proj.workdir or "", "SPEC.md"), os.path.join(proj.rundir or "", "SPEC.md")):
        if p and os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                spec = f.read()
            break
    if template and template != "auto":
        bundle, meta = propose(proj.home, spec, chat_fn=None, proj=None, template=template)
    else:
        bundle, meta = propose(proj.home, spec, chat_fn=chat_fn, proj=proj if chat_fn else None)
    path = write_project_pipeline(proj, bundle, template_meta=meta)
    return path, meta
