"""The SQLite schema, and reading the older JSON data file."""
import json
from .errors import StoreError

SCHEMA = ["CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
          "CREATE TABLE notes(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL)",
          "CREATE TABLE note_tags(note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE, position INTEGER NOT NULL, tag TEXT NOT NULL, PRIMARY KEY(note_id, tag))"]
SCHEMA_VERSION = 1

def read_legacy(path):
    """The (next_id, notes) of a stage 1/2 JSON file; StoreError when it is not one."""
    try:
        with open(path) as f: d = json.load(f)
        notes = [{"id": n["id"], "title": n["title"], "body": n["body"], "created": n["created"], "updated": n["updated"], "tags": list(n.get("tags", []))} for n in d["notes"]]
        if not all(isinstance(n["id"], int) and isinstance(n["title"], str) and isinstance(n["body"], str) for n in notes): raise ValueError("bad note")
        return int(d["next_id"]), notes
    except (ValueError, KeyError, TypeError, OSError, AttributeError) as e: raise StoreError(f"data file is corrupt: {path}") from e

def create_database(db, next_id, notes):
    for sql in SCHEMA: db.execute(sql)
    db.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    for n in notes:
        db.execute("INSERT INTO notes(id, title, body, created, updated) VALUES (?, ?, ?, ?, ?)", (n["id"], n["title"], n["body"], n["created"], n["updated"]))
        for i, t in enumerate(n["tags"]): db.execute("INSERT INTO note_tags(note_id, position, tag) VALUES (?, ?, ?)", (n["id"], i, t))
    seq = max(next_id - 1, max((n["id"] for n in notes), default=0))
    if db.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'notes'", (seq,)).rowcount == 0: db.execute("INSERT INTO sqlite_sequence(name, seq) VALUES ('notes', ?)", (seq,))
