"""Enforce agent sees.read / sees.write on workspace paths. Harness-side, not prompt-side."""
import os, fnmatch
from .validate import ValidationError

def _norm(rel):
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"): raise ValidationError([f"unsafe path {rel!r}"])
    return rel

def _match(rel, patterns):
    rel = _norm(rel)
    for p in patterns or []:
        p = p.replace("\\", "/")
        if p in ("*", "**"): return True
        if p.endswith("/**"):
            root = p[:-3].rstrip("/")
            if rel == root or rel.startswith(root + "/"): return True
            continue
        if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p): return True
        if rel == p: return True
    return False

def can_read(agent, rel):
    sees = (agent or {}).get("sees") or {}
    return _match(rel, sees.get("read") or [])

def can_write(agent, rel):
    sees = (agent or {}).get("sees") or {}
    return _match(rel, sees.get("write") or []) and can_read(agent, rel)

def resolve(workspace, rel):
    """Absolute path under workspace for a relative path; refuses escape."""
    rel = _norm(rel)
    root = os.path.realpath(workspace)
    path = os.path.realpath(os.path.join(root, rel))
    if path != root and not path.startswith(root + os.sep): raise ValidationError([f"path escapes workspace: {rel!r}"])
    return path

def read_text(workspace, agent, rel, limit=200_000):
    if not can_read(agent, rel): raise ValidationError([f"read denied: {rel}"])
    path = resolve(workspace, rel)
    with open(path, encoding="utf-8", errors="replace") as f: return f.read(limit)

def write_text(workspace, agent, rel, text):
    if not can_write(agent, rel): raise ValidationError([f"write denied: {rel}"])
    path = resolve(workspace, rel)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: f.write(text)
    os.replace(tmp, path)
    return rel
