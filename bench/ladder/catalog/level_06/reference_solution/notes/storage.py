import json, os, tempfile, time
from .models import Note
from .validation import clean_title, clean_body, clean_tags

class NoteNotFound(KeyError):
    pass

class StorageError(Exception):
    pass

def _now(): return time.strftime("%Y-%m-%dT%H:%M:%S")

class NoteStore:
    def __init__(self, path, clock=None):
        self.path, self.clock = path, clock or _now
        self._notes, self._next = {}, 1
        if os.path.exists(path): self._load()

    def _load(self):
        try:
            with open(self.path) as f: d = json.load(f)
            self._next = int(d["next_id"]); notes = [Note.from_dict(n) for n in d["notes"]]
            if not isinstance(self._next, int) or self._next < 1: raise ValueError("next_id")
        except (ValueError, KeyError, TypeError, OSError) as e: raise StorageError(f"data file is corrupt: {self.path}") from e
        self._notes = {n.id: n for n in notes}

    def _save(self):
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.path)), prefix=".notes-")
        with os.fdopen(fd, "w") as f: json.dump({"next_id": self._next, "notes": [n.to_dict() for n in self.all()]}, f, indent=1)
        os.replace(tmp, self.path)

    def add(self, title, body="", tags=(), pinned=False):
        title, body, tags = clean_title(title), clean_body(body), clean_tags(tags); now = self.clock()
        n = Note(self._next, title, body, tags, now, now, bool(pinned)); self._notes[n.id] = n; self._next += 1; self._save(); return n

    def restore(self, note):
        """Insert a Note as it is (id and timestamps kept). The id must be free; next_id moves past it so ids are never reused."""
        if note.id in self._notes: raise ValueError(f"note {note.id} already exists")
        note = Note.from_dict(note.to_dict()); self._notes[note.id] = note; self._next = max(self._next, note.id + 1); self._save(); return note

    def get(self, note_id):
        if note_id not in self._notes: raise NoteNotFound(note_id)
        return self._notes[note_id]

    def update(self, note_id, **fields):
        n = self.get(note_id); unknown = set(fields) - {"title", "body", "tags", "pinned"}
        if unknown: raise TypeError(f"unknown field(s): {', '.join(sorted(unknown))}")
        new = {"title": clean_title(fields["title"]) if "title" in fields else n.title, "body": clean_body(fields["body"]) if "body" in fields else n.body,
               "tags": clean_tags(fields["tags"]) if "tags" in fields else n.tags, "pinned": bool(fields["pinned"]) if "pinned" in fields else n.pinned}
        for k, v in new.items(): setattr(n, k, v)
        n.updated = self.clock(); self._save(); return n

    def delete(self, note_id):
        n = self.get(note_id); del self._notes[note_id]; self._save(); return n

    def all(self): return [self._notes[i] for i in sorted(self._notes)]
    def __len__(self): return len(self._notes)
