"""Load pipeline JSON from disk and resolve $ref-style relative paths into one bundle."""
import json, os
from .validate import validate_doc, validate_project, ValidationError

def bundle_root(path):
    """Directory that owns agents/teams/workflows/projects (or the file's own directory)."""
    d = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    leaf = os.path.basename(d)
    if leaf in ("projects", "agents", "teams", "workflows", "artifacts"): return os.path.dirname(d)
    return d

def resolve_path(base, rel, root=None):
    """Resolve $ref against the bundle root. Refuse paths that escape that root (including via ..)."""
    root = os.path.realpath(root or bundle_root(base))
    path = os.path.realpath(os.path.join(root, rel))
    if path != root and not path.startswith(root + os.sep):
        raise ValidationError([f"ref escapes template directory: {rel!r}"])
    return path

def load_doc(path):
    with open(path, encoding="utf-8") as f: doc = json.load(f)
    if not isinstance(doc, dict): raise ValidationError([f"{path}: root must be an object"])
    return doc

def _deref(node, base_path, cache, root=None):
    """If node is {"$ref": "rel.json"}, load and return that document; else return node."""
    if not isinstance(node, dict) or "$ref" not in node: return node
    if len(node) != 1: raise ValidationError(["$ref object must contain only $ref"])
    path = resolve_path(base_path, node["$ref"], root=root)
    if path not in cache: cache[path] = load_doc(path)
    return cache[path]

def load_project(path):
    """Load a project JSON (possibly with $ref to agents/team/workflow) into a validated bundle."""
    path = os.path.abspath(path)
    root_doc = load_doc(path)
    cache = {path: root_doc}
    broot = bundle_root(path)
    if root_doc.get("kind") == "project" or "team" in root_doc or "workflow" in root_doc:
        agents = {}
        raw_agents = root_doc.get("agents") or {}
        if isinstance(raw_agents, dict):
            for k, v in raw_agents.items():
                doc = _deref(v, path, cache, root=broot)
                if doc.get("kind") and doc.get("kind") != "agent":
                    raise ValidationError([f"agents.{k}: $ref is not an agent"])
                agents[doc.get("id") or k] = doc
        elif isinstance(raw_agents, list):
            for i, v in enumerate(raw_agents):
                doc = _deref(v, path, cache, root=broot)
                if not doc.get("id"): raise ValidationError([f"agents[{i}]: missing id"])
                agents[doc["id"]] = doc
        team = _deref(root_doc.get("team"), path, cache, root=broot) if root_doc.get("team") is not None else None
        workflow = _deref(root_doc.get("workflow"), path, cache, root=broot) if root_doc.get("workflow") is not None else None
        project = {k: v for k, v in root_doc.items() if k not in ("agents", "team", "workflow")}
        if "kind" not in project: project["kind"] = "project"
        if "schema" not in project: project["schema"] = root_doc.get("schema")
        bundle = {"project": project, "agents": agents, "team": team, "workflow": workflow, "ui": root_doc.get("ui")}
        return validate_project(bundle)
    raise ValidationError([f"{path}: expected a project document"])

def load_and_validate(path):
    """Load any pipeline document and validate it (agents/teams/workflows standalone; projects resolved)."""
    doc = load_doc(path)
    if doc.get("kind") == "project" or (doc.get("team") and doc.get("workflow")):
        return load_project(path)
    return validate_doc(doc)
