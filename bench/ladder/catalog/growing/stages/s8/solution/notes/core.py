"""The store: opening and upgrading the database, transactions, access rules and the notes themselves."""
import json, os, secrets, shutil, sqlite3, tempfile, threading, time
from .errors import NotFound, PermissionDenied, StoreError
from .schema import create_database, read_legacy
from .validation import valid_body, valid_tags, valid_title

def now(): return time.strftime("%Y-%m-%dT%H:%M:%S")

class StoreCore:
    def __init__(self, path, clock=None, device_id=None):
        self._want_device = device_id
        self.path, self.clock = path, clock or (lambda: os.environ.get("NOTES_NOW") or now()); self._lock = threading.RLock()
        if not os.path.exists(path):                                   # a new database is built beside the path and linked in atomically, so nobody ever sees a half-made file
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".notes-"); os.close(fd); os.remove(tmp)
            db = sqlite3.connect(tmp, isolation_level=None)
            try: db.execute("BEGIN"); create_database(db, 1, []); db.execute("COMMIT")
            finally: db.close()
            try: os.link(tmp, path)
            except FileExistsError: pass
            finally: os.remove(tmp)
        if True:
            with open(path, "rb") as f: head = f.read(16)
            if not head.startswith(b"SQLite format 3"):
                next_id, notes = read_legacy(path)                              # a stage 1/2 JSON file: migrate it, keeping the original
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".notes-"); os.close(fd); os.remove(tmp)
                db = sqlite3.connect(tmp, isolation_level=None)
                try: db.execute("BEGIN"); create_database(db, next_id, notes); db.execute("COMMIT")
                finally: db.close()
                shutil.copyfile(path, path + ".json.bak"); os.replace(tmp, path)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False); self.db.execute("PRAGMA foreign_keys = ON")
        try:
            have = self.db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if have is None: raise sqlite3.DatabaseError("no schema")
        except sqlite3.DatabaseError as e:
            if os.path.getsize(path) == 0 or "no such table" in str(e) or "no schema" in str(e) or "not a database" in str(e) or "malformed" in str(e):
                self.db.close(); raise StoreError(f"data file is corrupt: {path}") from e
            raise
        self._upgrade()

    def _upgrade(self):
        """Older databases (stages 3 and 4) get the new tables and the owner column when they are opened. The schema version stays 1."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            cols = [r[1] for r in self.db.execute("PRAGMA table_info(notes)")]
            for col in ("owner", "reminder", "reminder_fired"):
                if col not in cols: self.db.execute(f"ALTER TABLE notes ADD COLUMN {col} TEXT")
            self.db.execute("CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT, note_id INTEGER NOT NULL, user TEXT COLLATE NOCASE, message TEXT NOT NULL, ts TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0)")
            self.db.execute("CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY COLLATE NOCASE, pw_hash TEXT NOT NULL, created TEXT NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY, user TEXT NOT NULL, created TEXT NOT NULL, expires TEXT NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS shares(note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE, user TEXT NOT NULL COLLATE NOCASE, mode TEXT NOT NULL CHECK(mode IN ('read', 'write')), PRIMARY KEY(note_id, user))")
            cols = [r[1] for r in self.db.execute("PRAGMA table_info(notes)")]
            for col in ("uid", "device", "synced"):
                if col not in cols: self.db.execute(f"ALTER TABLE notes ADD COLUMN {col} TEXT")
            have = self.db.execute("SELECT value FROM meta WHERE key = 'device_id'").fetchone()
            dev = have[0] if have else (self._want_device or secrets.token_hex(4))
            if not have: self.db.execute("INSERT INTO meta(key, value) VALUES ('device_id', ?)", (dev,))
            for (nid,) in self.db.execute("SELECT id FROM notes WHERE uid IS NULL").fetchall(): self.db.execute("UPDATE notes SET uid = ?, device = ? WHERE id = ?", (secrets.token_hex(8), dev, nid))
            self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS notes_uid ON notes(uid)")
            self.db.execute("CREATE TABLE IF NOT EXISTS tombstones(uid TEXT PRIMARY KEY, deleted TEXT NOT NULL, device TEXT NOT NULL)")
            self.db.execute("COMMIT")
        except BaseException: self.db.execute("ROLLBACK"); raise
        self.device_id = dev

    def schema_version(self): return int(self.db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0])

    def _tx(self, fn):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try: out = fn(); self.db.execute("COMMIT"); return out
            except BaseException: self.db.execute("ROLLBACK"); raise

    COLS = "id, title, body, created, updated, owner, reminder, uid, device"

    def _note(self, row):
        return {"id": row[0], "title": row[1], "body": row[2], "tags": [t[0] for t in self.db.execute("SELECT tag FROM note_tags WHERE note_id = ? ORDER BY position", (row[0],))], "owner": row[5], "reminder": row[6], "uid": row[7], "device": row[8], "created": row[3], "updated": row[4]}

    def _row(self, note_id):
        r = self.db.execute(f"SELECT {self.COLS} FROM notes WHERE id = ?", (note_id,)).fetchone() if isinstance(note_id, int) and not isinstance(note_id, bool) else None
        if r is None: raise NotFound(note_id)
        return r

    def _user(self, actor):
        r = self.db.execute("SELECT name FROM users WHERE name = ?", (actor,)).fetchone() if isinstance(actor, str) else None
        if r is None: raise NotFound(f"user {actor}")
        return r[0]

    def _access(self, row, actor):
        """'owner', 'write', 'read' or None for this actor. actor=None is the unrestricted local mode; notes without an owner belong to everyone."""
        if actor is None or row[5] is None or row[5].lower() == actor.lower(): return "owner"
        r = self.db.execute("SELECT mode FROM shares WHERE note_id = ? AND user = ?", (row[0], actor)).fetchone()
        return r[0] if r else None

    def _visible(self, note_id, actor, need="read"):
        """The row of a note the actor may use for `need` (read, write or owner); NotFound if it is not visible, PermissionDenied if it is visible but not enough."""
        r = self._row(note_id)
        if actor is not None: actor = self._user(actor)
        a = self._access(r, actor)
        if a is None: raise NotFound(note_id)
        rank = {"read": 1, "write": 2, "owner": 3}
        if rank[a] < rank[need]: raise PermissionDenied(f"{actor} may not {'change' if need == 'write' else 'delete or share'} note {note_id}")
        return r

    # ---- notes ----
    def add(self, title, body="", tags=(), actor=None):
        title, body, tags = valid_title(title), valid_body(body), valid_tags(tags); now = self.clock()
        def go():
            owner = self._user(actor) if actor is not None else None
            nid = self.db.execute("INSERT INTO notes(title, body, created, updated, owner, uid, device) VALUES (?, ?, ?, ?, ?, ?, ?)", (title, body, now, now, owner, secrets.token_hex(8), self.device_id)).lastrowid
            for i, t in enumerate(tags): self.db.execute("INSERT INTO note_tags(note_id, position, tag) VALUES (?, ?, ?)", (nid, i, t))
            return self._note(self._row(nid))
        return self._tx(go)

    def get(self, note_id, actor=None):
        with self._lock: return self._note(self._visible(note_id, actor))

    def list(self, actor=None):
        with self._lock:
            a = self._user(actor) if actor is not None else None
            return [self._note(r) for r in self.db.execute(f"SELECT {self.COLS} FROM notes ORDER BY id").fetchall() if self._access(r, a)]

    def update(self, note_id, title=None, body=None, tags=None, actor=None):
        def go():
            r = self._visible(note_id, actor, "write"); t = valid_title(title) if title is not None else r[1]; b = valid_body(body) if body is not None else r[2]; tg = valid_tags(tags) if tags is not None else None
            self.db.execute("UPDATE notes SET title = ?, body = ?, updated = ?, device = ? WHERE id = ?", (t, b, self.clock(), self.device_id, note_id))
            if tg is not None:
                self.db.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
                for i, x in enumerate(tg): self.db.execute("INSERT INTO note_tags(note_id, position, tag) VALUES (?, ?, ?)", (note_id, i, x))
            return self._note(self._row(note_id))
        return self._tx(go)

    def delete(self, note_id, actor=None):
        def go():
            n = self._note(self._visible(note_id, actor, "owner")); self.db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            self.db.execute("INSERT OR REPLACE INTO tombstones(uid, deleted, device) VALUES (?, ?, ?)", (n["uid"], self.clock(), self.device_id)); return n
        return self._tx(go)

    def search(self, query="", tags=(), actor=None):
        """Notes that contain every word of the query (title or body, ignoring case) and carry every given tag, ordered by id (only the ones the actor may read)."""
        words, need = str(query).lower().split(), valid_tags(tags)
        return [n for n in self.list(actor) if all(w in (n["title"] + "\n" + n["body"]).lower() for w in words) and all(t in n["tags"] for t in need)]

    def tags(self, actor=None):
        counts = {}
        for n in self.list(actor):
            for t in n["tags"]: counts[t] = counts.get(t, 0) + 1
        return sorted(counts.items())

    def stats(self):
        with self._lock: return {"notes": self.db.execute("SELECT COUNT(*) FROM notes").fetchone()[0], "tags": self.db.execute("SELECT COUNT(DISTINCT tag) FROM note_tags").fetchone()[0]}

    def export_json(self, path):
        """Write all notes in the stage 2 JSON file format (for other tools) atomically; returns the number of notes."""
        with self._lock:
            notes = self.list(); seq = self.db.execute("SELECT seq FROM sqlite_sequence WHERE name = 'notes'").fetchone()
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".export-")
        with os.fdopen(fd, "w") as f: json.dump({"next_id": (seq[0] if seq else 0) + 1, "notes": [{k: v for k, v in n.items() if k not in ("owner", "reminder", "uid", "device")} for n in notes]}, f)
        os.replace(tmp, path); return len(notes)

    def close(self): self.db.close()
