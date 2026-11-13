"""Versioned artifact store under the pipeline run directory."""
import json, os, time, hashlib

def root(run_dir): return os.path.join(run_dir, "artifacts")

def put(run_dir, name, text, *, producer, kind="note", inv=None):
    """Write artifacts/<name>/vN.txt and an index row. Returns metadata. With inv (the model turn that made it), the same turn carried out again
    after a crash gets the version it already wrote."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:80] or "artifact"
    if inv:
        for a in list_artifacts(run_dir):
            if a.get("inv") == inv and a.get("name") == name: return a
    d = os.path.join(root(run_dir), safe); os.makedirs(d, exist_ok=True)
    version = 1 + sum(1 for f in os.listdir(d) if f.startswith("v") and f.endswith(".txt"))
    abs_path = os.path.join(d, f"v{version}.txt")
    with open(abs_path, "w", encoding="utf-8") as f: f.write(text)
    meta = {**({"inv": inv} if inv else {}), "id": f"{safe}-v{version}", "name": name, "kind": kind, "version": version,
            "producer": producer, "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "bytes": len(text.encode()), "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "path": os.path.relpath(abs_path, run_dir)}
    from .journal import append_line
    append_line(os.path.join(root(run_dir), "index.jsonl"), json.dumps(meta))
    return meta

def list_artifacts(run_dir):
    idx = os.path.join(root(run_dir), "index.jsonl")
    if not os.path.isfile(idx): return []
    out = []
    with open(idx, encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                try: out.append(json.loads(ln))
                except ValueError: pass
    return out

def read(run_dir, meta_or_path):
    path = meta_or_path["path"] if isinstance(meta_or_path, dict) else meta_or_path
    with open(os.path.join(run_dir, path), encoding="utf-8") as f: return f.read()
