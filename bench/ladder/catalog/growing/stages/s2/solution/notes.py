"""Notes application, stage 2: tags and search."""
import argparse, json, os, re, sys, tempfile, time

class NotesError(Exception): pass
class ValidationError(NotesError, ValueError): pass
class NotFound(NotesError, KeyError): pass
class StoreError(NotesError): pass

def _now(): return time.strftime("%Y-%m-%dT%H:%M:%S")

def _title(v):
    if not isinstance(v, str) or not v.strip(): raise ValidationError("title must not be empty")
    v = v.strip()
    if len(v) > 100 or "\n" in v: raise ValidationError("title must be one line of at most 100 characters")
    return v

def _body(v):
    if not isinstance(v, str): raise ValidationError("body must be a string")
    if len(v) > 5000: raise ValidationError("body must be at most 5000 characters")
    return v

TAG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,19}$")

def _tags(v):
    if isinstance(v, str) or not hasattr(v, "__iter__"): raise ValidationError("tags must be a list of strings")
    out = []
    for t in v:
        if not isinstance(t, str): raise ValidationError("tags must be a list of strings")
        t = t.strip().lower()
        if not TAG.match(t): raise ValidationError(f"invalid tag '{t}'")
        if t not in out: out.append(t)
    if len(out) > 10: raise ValidationError("at most 10 tags are allowed")
    return out

class NoteStore:
    def __init__(self, path, clock=None):
        self.path, self.clock = path, clock or (lambda: os.environ.get("NOTES_NOW") or _now())
        self.next_id, self.notes = 1, {}
        if os.path.exists(path):
            try:
                with open(path) as f: d = json.load(f)
                self.next_id = int(d["next_id"]); self.notes = {n["id"]: {**n, "tags": list(n.get("tags", []))} for n in d["notes"]}
            except (ValueError, KeyError, TypeError, OSError) as e: raise StoreError(f"data file is corrupt: {path}") from e

    def _save(self):
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.path)), prefix=".notes-")
        with os.fdopen(fd, "w") as f: json.dump({"next_id": self.next_id, "notes": [self.notes[i] for i in sorted(self.notes)]}, f)
        os.replace(tmp, self.path)

    def add(self, title, body="", tags=()):
        title, body, tags = _title(title), _body(body), _tags(tags); now = self.clock()
        n = {"id": self.next_id, "title": title, "body": body, "tags": tags, "created": now, "updated": now}
        self.notes[n["id"]] = n; self.next_id += 1; self._save(); return dict(n)

    def get(self, note_id):
        if note_id not in self.notes: raise NotFound(note_id)
        return dict(self.notes[note_id])

    def list(self): return [dict(self.notes[i]) for i in sorted(self.notes)]

    def update(self, note_id, title=None, body=None, tags=None):
        n = self.get(note_id)
        if title is not None: n["title"] = _title(title)
        if body is not None: n["body"] = _body(body)
        if tags is not None: n["tags"] = _tags(tags)
        n["updated"] = self.clock(); self.notes[note_id] = n; self._save(); return dict(n)

    def search(self, query="", tags=()):
        """Notes that contain every word of the query (title or body, ignoring case) and carry every given tag, ordered by id."""
        words, need = str(query).lower().split(), _tags(tags)
        return [n for n in self.list() if all(w in (n["title"] + "\n" + n["body"]).lower() for w in words) and all(t in n["tags"] for t in need)]

    def tags(self):
        """Every tag in use with the number of notes that have it, as sorted (tag, count) pairs."""
        counts = {}
        for n in self.notes.values():
            for t in n["tags"]: counts[t] = counts.get(t, 0) + 1
        return sorted(counts.items())

    def delete(self, note_id):
        n = self.get(note_id); del self.notes[note_id]; self._save(); return n

def line(n): return f"{n['id']:>3}  {n['title']}" + (("  " + " ".join("#" + t for t in n["tags"])) if n.get("tags") else "")

def main(argv=None, out=None):
    out = out or sys.stdout; say = lambda s="": print(s, file=out)
    ap = argparse.ArgumentParser(prog="notes"); sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("add"); a.add_argument("title"); a.add_argument("--body", default=""); a.add_argument("--tag", action="append", default=[])
    l = sub.add_parser("list"); l.add_argument("--tag", action="append", default=[]); sub.add_parser("show").add_argument("id", type=int); sub.add_parser("delete").add_argument("id", type=int)
    e = sub.add_parser("edit"); e.add_argument("id", type=int); e.add_argument("--title"); e.add_argument("--body"); e.add_argument("--tag", action="append")
    q = sub.add_parser("search"); q.add_argument("words", nargs="+"); q.add_argument("--tag", action="append", default=[]); sub.add_parser("tags")
    try:
        ns = ap.parse_args(argv)
        if not ns.cmd: ap.print_usage(sys.stderr); return 2
        s = NoteStore(os.environ.get("NOTES_FILE") or "notes.json")
        if ns.cmd == "add": say(f"Added note {s.add(ns.title, ns.body, ns.tag)['id']}")
        elif ns.cmd == "list": ns_ = s.search("", ns.tag); say("\n".join(line(n) for n in ns_) if ns_ else "No notes.")
        elif ns.cmd == "search": ns_ = s.search(" ".join(ns.words), ns.tag); say("\n".join(line(n) for n in ns_) if ns_ else "No matches.")
        elif ns.cmd == "tags": ts = s.tags(); say("\n".join(f"{t}: {c}" for t, c in ts) if ts else "No tags.")
        elif ns.cmd == "show": n = s.get(ns.id); say(n["title"]); say(n["body"])
        elif ns.cmd == "delete": say(f"Deleted note {s.delete(ns.id)['id']}")
        elif ns.cmd == "edit":
            if ns.title is None and ns.body is None and ns.tag is None: print("Error: nothing to edit", file=sys.stderr); return 2
            say(f"Updated note {s.update(ns.id, ns.title, ns.body, ns.tag)['id']}")
        return 0
    except NotFound as e: print(f"Error: no note with id {e.args[0]}", file=sys.stderr); return 1
    except (ValidationError, StoreError) as e: print(f"Error: {e}", file=sys.stderr); return 2
    except SystemExit as e: return e.code if isinstance(e.code, int) else 2

if __name__ == "__main__": sys.exit(main())
