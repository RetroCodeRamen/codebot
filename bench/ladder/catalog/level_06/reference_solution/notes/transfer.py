import json, os, tempfile
from .models import Note
from .validation import ValidationError, clean_title, clean_body, clean_tags

class TransferError(Exception):
    pass

def _write(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".notes-")
    with os.fdopen(fd, "w") as f: f.write(text)
    os.replace(tmp, path)

def export_json(store, path):
    notes = store.all(); _write(path, json.dumps({"version": 1, "notes": [n.to_dict() for n in notes]}, indent=1)); return len(notes)

def export_markdown(store, path):
    notes = sorted(store.all(), key=lambda n: (not n.pinned, n.id)); parts = []
    for n in notes:
        p = [f"# {n.title}", ""]
        if n.tags: p += [f"Tags: {', '.join(n.tags)}", ""]
        if n.body: p += [n.body, ""]
        parts.append("\n".join(p) + "\n---\n")
    _write(path, "\n".join(parts)); return len(notes)

def import_json(store, path, on_conflict="skip"):
    if on_conflict not in ("skip", "overwrite", "duplicate"): raise ValueError(f"invalid on_conflict '{on_conflict}'")
    try:
        with open(path) as f: d = json.load(f)
        raw = d["notes"]
        assert d.get("version") == 1 and isinstance(raw, list)
    except (OSError,): raise
    except (ValueError, KeyError, AssertionError, TypeError): raise TransferError("not a valid notes export file")
    cleaned = []
    for i, r in enumerate(raw):
        try:
            if not isinstance(r, dict) or not isinstance(r.get("id"), int) or isinstance(r.get("id"), bool): raise ValidationError("id must be an integer")
            cleaned.append((r["id"], clean_title(r.get("title")), clean_body(r.get("body", "")), clean_tags(r.get("tags", [])), bool(r.get("pinned", False)), r.get("created") or "", r.get("updated") or ""))
        except ValidationError as e: raise TransferError(f"note {i + 1}: {e}") from e
    res = {"added": 0, "skipped": 0, "overwritten": 0}
    for nid, title, body, tags, pinned, created, updated in cleaned:
        exists = any(n.id == nid for n in store.all())
        if exists and on_conflict == "skip": res["skipped"] += 1
        elif exists and on_conflict == "overwrite": store.update(nid, title=title, body=body, tags=tags, pinned=pinned); res["overwritten"] += 1
        elif exists: store.add(title, body, tags, pinned); res["added"] += 1                       # duplicate: a new id
        else: store.restore(Note(nid, title, body, tags, created, updated, pinned)); res["added"] += 1
    return res
