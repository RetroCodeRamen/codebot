"""Notes application, stage 7: synchronisation between devices."""
import argparse, datetime, hashlib, hmac, json, os, re, secrets, shutil, sqlite3, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

class NotesError(Exception): pass
class ValidationError(NotesError, ValueError): pass
class NotFound(NotesError, KeyError): pass
class StoreError(NotesError): pass
class AuthError(NotesError): pass
class PermissionDenied(NotesError): pass

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

SCHEMA = ["CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
          "CREATE TABLE notes(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL)",
          "CREATE TABLE note_tags(note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE, position INTEGER NOT NULL, tag TEXT NOT NULL, PRIMARY KEY(note_id, tag))"]
SCHEMA_VERSION = 1

def _read_legacy(path):
    """The (next_id, notes) of a stage 1/2 JSON file; StoreError when it is not one."""
    try:
        with open(path) as f: d = json.load(f)
        notes = [{"id": n["id"], "title": n["title"], "body": n["body"], "created": n["created"], "updated": n["updated"], "tags": list(n.get("tags", []))} for n in d["notes"]]
        if not all(isinstance(n["id"], int) and isinstance(n["title"], str) and isinstance(n["body"], str) for n in notes): raise ValueError("bad note")
        return int(d["next_id"]), notes
    except (ValueError, KeyError, TypeError, OSError, AttributeError) as e: raise StoreError(f"data file is corrupt: {path}") from e

def _create(db, next_id, notes):
    for sql in SCHEMA: db.execute(sql)
    db.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    for n in notes:
        db.execute("INSERT INTO notes(id, title, body, created, updated) VALUES (?, ?, ?, ?, ?)", (n["id"], n["title"], n["body"], n["created"], n["updated"]))
        for i, t in enumerate(n["tags"]): db.execute("INSERT INTO note_tags(note_id, position, tag) VALUES (?, ?, ?)", (n["id"], i, t))
    seq = max(next_id - 1, max((n["id"] for n in notes), default=0))
    if db.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'notes'", (seq,)).rowcount == 0: db.execute("INSERT INTO sqlite_sequence(name, seq) VALUES ('notes', ?)", (seq,))

class NoteStore:
    def __init__(self, path, clock=None, device_id=None):
        self._want_device = device_id
        self.path, self.clock = path, clock or (lambda: os.environ.get("NOTES_NOW") or _now()); self._lock = threading.RLock()
        if not os.path.exists(path):                                   # a new database is built beside the path and linked in atomically, so nobody ever sees a half-made file
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".notes-"); os.close(fd); os.remove(tmp)
            db = sqlite3.connect(tmp, isolation_level=None)
            try: db.execute("BEGIN"); _create(db, 1, []); db.execute("COMMIT")
            finally: db.close()
            try: os.link(tmp, path)
            except FileExistsError: pass
            finally: os.remove(tmp)
        if True:
            with open(path, "rb") as f: head = f.read(16)
            if not head.startswith(b"SQLite format 3"):
                next_id, notes = _read_legacy(path)                              # a stage 1/2 JSON file: migrate it, keeping the original
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".notes-"); os.close(fd); os.remove(tmp)
                db = sqlite3.connect(tmp, isolation_level=None)
                try: db.execute("BEGIN"); _create(db, next_id, notes); db.execute("COMMIT")
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

    # ---- users and tokens ----
    def create_user(self, name, password):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{1,29}", name): raise ValidationError("a user name has 2-30 characters: letters, digits, '.', '_' and '-', starting with a letter")
        if not isinstance(password, str) or len(password) < 8: raise ValidationError("the password must have at least 8 characters")
        def go():
            if self.db.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone(): raise ValidationError(f"user '{name}' already exists")
            salt = secrets.token_hex(8); h = salt + "$" + hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 20000).hex()
            self.db.execute("INSERT INTO users(name, pw_hash, created) VALUES (?, ?, ?)", (name, h, self.clock())); return {"name": name}
        return self._tx(go)

    def login(self, name, password):
        """A token valid for 24 hours of clock time."""
        with self._lock:
            r = self.db.execute("SELECT name, pw_hash FROM users WHERE name = ?", (name,)).fetchone() if isinstance(name, str) else None
            ok = r is not None and isinstance(password, str) and hmac.compare_digest(r[1].split("$")[0] + "$" + hashlib.pbkdf2_hmac("sha256", password.encode(), r[1].split("$")[0].encode(), 20000).hex(), r[1])
            if not ok: raise AuthError("invalid credentials")
            now = self.clock(); exp = (datetime.datetime.strptime(now, "%Y-%m-%dT%H:%M:%S") + datetime.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"); tok = secrets.token_hex(16)
        self._tx(lambda: self.db.execute("INSERT INTO tokens(token, user, created, expires) VALUES (?, ?, ?, ?)", (tok, r[0], now, exp))); return tok

    def user_for_token(self, token):
        with self._lock:
            r = self.db.execute("SELECT user, expires FROM tokens WHERE token = ?", (token,)).fetchone() if isinstance(token, str) else None
            if r is None or r[1] <= self.clock(): raise AuthError("invalid or expired token")
            return r[0]

    def logout(self, token): self._tx(lambda: self.db.execute("DELETE FROM tokens WHERE token = ?", (token,)))

    # ---- notes ----
    def add(self, title, body="", tags=(), actor=None):
        title, body, tags = _title(title), _body(body), _tags(tags); now = self.clock()
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
            r = self._visible(note_id, actor, "write"); t = _title(title) if title is not None else r[1]; b = _body(body) if body is not None else r[2]; tg = _tags(tags) if tags is not None else None
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
        words, need = str(query).lower().split(), _tags(tags)
        return [n for n in self.list(actor) if all(w in (n["title"] + "\n" + n["body"]).lower() for w in words) and all(t in n["tags"] for t in need)]

    def tags(self, actor=None):
        counts = {}
        for n in self.list(actor):
            for t in n["tags"]: counts[t] = counts.get(t, 0) + 1
        return sorted(counts.items())

    def stats(self):
        with self._lock: return {"notes": self.db.execute("SELECT COUNT(*) FROM notes").fetchone()[0], "tags": self.db.execute("SELECT COUNT(DISTINCT tag) FROM note_tags").fetchone()[0]}

    # ---- sharing ----
    def share(self, note_id, user, mode, actor=None):
        if mode not in ("read", "write"): raise ValidationError("mode must be 'read' or 'write'")
        def go():
            r = self._visible(note_id, actor, "owner"); u = self._user(user)
            if r[5] is not None and r[5].lower() == u.lower(): raise ValidationError("a note is always shared with its owner")
            self.db.execute("INSERT INTO shares(note_id, user, mode) VALUES (?, ?, ?) ON CONFLICT(note_id, user) DO UPDATE SET mode = excluded.mode", (note_id, u, mode)); return self._shares(note_id)
        return self._tx(go)

    def unshare(self, note_id, user, actor=None):
        def go():
            self._visible(note_id, actor, "owner")
            if self.db.execute("DELETE FROM shares WHERE note_id = ? AND user = ?", (note_id, user)).rowcount == 0: raise NotFound(f"share {user}")
            return self._shares(note_id)
        return self._tx(go)

    def _shares(self, note_id): return [{"user": u, "mode": m} for u, m in self.db.execute("SELECT user, mode FROM shares WHERE note_id = ? ORDER BY user COLLATE NOCASE", (note_id,))]

    def shares(self, note_id, actor=None):
        with self._lock: self._visible(note_id, actor, "owner"); return self._shares(note_id)

    # ---- reminders and notifications ----
    def set_reminder(self, note_id, due, actor=None):
        if not isinstance(due, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", due): raise ValidationError("a reminder time looks like 2024-03-01T09:00:00")
        try: datetime.datetime.strptime(due, "%Y-%m-%dT%H:%M:%S")
        except ValueError: raise ValidationError("a reminder time looks like 2024-03-01T09:00:00")
        def go():
            self._visible(note_id, actor, "write"); self.db.execute("UPDATE notes SET reminder = ?, reminder_fired = NULL WHERE id = ?", (due, note_id)); return self._note(self._row(note_id))
        return self._tx(go)

    def clear_reminder(self, note_id, actor=None):
        def go():
            self._visible(note_id, actor, "write"); self.db.execute("UPDATE notes SET reminder = NULL, reminder_fired = NULL WHERE id = ?", (note_id,)); return self._note(self._row(note_id))
        return self._tx(go)

    def fire_due(self, now=None):
        """Turn every reminder that is due (due <= now) and not yet fired into a notification for the note's owner. Atomic and idempotent: each reminder fires exactly once,
        also when several workers or processes run this at the same time. Returns the ids of the notes whose reminder fired, in id order."""
        now = now or self.clock()
        def go():
            fired = []
            for nid, title, owner in self.db.execute("SELECT id, title, owner FROM notes WHERE reminder IS NOT NULL AND reminder_fired IS NULL AND reminder <= ? ORDER BY id", (now,)).fetchall():
                if self.db.execute("UPDATE notes SET reminder_fired = ? WHERE id = ? AND reminder_fired IS NULL", (now, nid)).rowcount:
                    self.db.execute("INSERT INTO notifications(note_id, user, message, ts) VALUES (?, ?, ?, ?)", (nid, owner, f"Reminder: {title}", now)); fired.append(nid)
            return fired
        return self._tx(go)

    def purge_tokens(self, now=None):
        now = now or self.clock(); return self._tx(lambda: self.db.execute("DELETE FROM tokens WHERE expires <= ?", (now,)).rowcount)

    def notifications(self, user=None, unread_only=False):
        """Notifications in id order; with `user` only that user's (matching ignoring case), otherwise all."""
        with self._lock:
            q, a = "SELECT id, note_id, user, message, ts, read FROM notifications WHERE 1 = 1", []
            if user is not None: q += " AND user = ?"; a.append(user)
            if unread_only: q += " AND read = 0"
            return [{"id": r[0], "note_id": r[1], "user": r[2], "message": r[3], "ts": r[4], "read": bool(r[5])} for r in self.db.execute(q + " ORDER BY id", a)]

    def mark_read(self, ids, user=None):
        """Mark the given notifications as read (only the user's own when `user` is given); returns how many changed."""
        ids = [i for i in ids if isinstance(i, int) and not isinstance(i, bool)]
        def go():
            n = 0
            for i in ids: n += self.db.execute("UPDATE notifications SET read = 1 WHERE id = ? AND read = 0" + (" AND user = ?" if user is not None else ""), (i, user) if user is not None else (i,)).rowcount
            return n
        return self._tx(go)

    # ---- synchronisation ----
    def snapshot(self):
        """Everything another device needs to merge with this one: the notes (without owner, shares and reminders, which stay on the device) and the tombstones of deleted notes."""
        with self._lock:
            notes = [{k: n[k] for k in ("uid", "title", "body", "tags", "created", "updated", "device")} for n in self.list()]
            tombs = [{"uid": r[0], "deleted": r[1], "device": r[2]} for r in self.db.execute("SELECT uid, deleted, device FROM tombstones ORDER BY uid")]
            return {"device": self.device_id, "notes": sorted(notes, key=lambda n: n["uid"]), "tombstones": tombs}

    @staticmethod
    def _check_snapshot(snap):
        stamp = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        def ok(v): return isinstance(v, str) and bool(stamp.fullmatch(v))
        if not isinstance(snap, dict) or not isinstance(snap.get("device"), str) or not isinstance(snap.get("notes"), list) or not isinstance(snap.get("tombstones"), list): raise ValidationError("not a snapshot")
        notes, tombs = [], []
        for n in snap["notes"]:
            if not isinstance(n, dict) or not isinstance(n.get("uid"), str) or not n["uid"] or not isinstance(n.get("device"), str) or not ok(n.get("created")) or not ok(n.get("updated")): raise ValidationError("invalid note in snapshot")
            notes.append({"uid": n["uid"], "title": _title(n.get("title")), "body": _body(n.get("body")), "tags": _tags(n.get("tags")), "created": n["created"], "updated": n["updated"], "device": n["device"]})
        for t in snap["tombstones"]:
            if not isinstance(t, dict) or not isinstance(t.get("uid"), str) or not t["uid"] or not isinstance(t.get("device"), str) or not ok(t.get("deleted")): raise ValidationError("invalid tombstone in snapshot")
            tombs.append({"uid": t["uid"], "deleted": t["deleted"], "device": t["device"]})
        return notes, tombs

    def _edited(self, uid):
        """True when this device changed the note since it last agreed on its content with another device."""
        r = self.db.execute("SELECT updated, device, synced FROM notes WHERE uid = ?", (uid,)).fetchone()
        return r is not None and r[2] != r[0] + "|" + r[1]

    def _mark_synced(self, uids):
        def go():
            for u in uids: self.db.execute("UPDATE notes SET synced = updated || '|' || device WHERE uid = ?", (u,))
        self._tx(go)

    def apply_snapshot(self, snap):
        """Merge a snapshot into this store (last writer wins, ordered by (time, device), a delete is a change like any other). Nothing is applied when the snapshot is invalid.
        Returns {"applied": notes created, updated or deleted here, "conflicts": notes whose two live versions differed and one was discarded}."""
        notes, tombs = self._check_snapshot(snap)
        if snap["device"] == self.device_id: raise ValidationError("a device cannot sync with itself")
        def go():
            applied = conflicts = 0
            def local(uid):
                r = self.db.execute(f"SELECT {self.COLS} FROM notes WHERE uid = ?", (uid,)).fetchone()
                t = self.db.execute("SELECT deleted, device FROM tombstones WHERE uid = ?", (uid,)).fetchone()
                return (self._note(r) if r else None), t
            def put(n, row):
                stamp = n["updated"] + "|" + n["device"]
                if row is None:
                    nid = self.db.execute("INSERT INTO notes(title, body, created, updated, owner, uid, device, synced) VALUES (?, ?, ?, ?, NULL, ?, ?, ?)", (n["title"], n["body"], n["created"], n["updated"], n["uid"], n["device"], stamp)).lastrowid
                else:
                    nid = row["id"]; self.db.execute("UPDATE notes SET title = ?, body = ?, created = ?, updated = ?, device = ?, synced = ? WHERE id = ?", (n["title"], n["body"], n["created"], n["updated"], n["device"], stamp, nid))
                    self.db.execute("DELETE FROM note_tags WHERE note_id = ?", (nid,))
                for i, t in enumerate(n["tags"]): self.db.execute("INSERT INTO note_tags(note_id, position, tag) VALUES (?, ?, ?)", (nid, i, t))
            for n in notes:
                row, tomb = local(n["uid"]); rk = (n["updated"], n["device"])
                if row is not None:
                    lk = (row["updated"], row["device"])
                    if rk > lk:
                        if (n["title"], n["body"], n["tags"]) != (row["title"], row["body"], row["tags"]) and self._edited(row["uid"]): conflicts += 1
                        put(n, row); applied += 1
                    elif rk == lk: self.db.execute("UPDATE notes SET synced = ? WHERE id = ?", (row["updated"] + "|" + row["device"], row["id"]))
                elif tomb is not None:
                    if rk > (tomb[0], tomb[1]): self.db.execute("DELETE FROM tombstones WHERE uid = ?", (n["uid"],)); put(n, None); applied += 1
                else: put(n, None); applied += 1
            for t in tombs:
                row, tomb = local(t["uid"]); xk = (t["deleted"], t["device"])
                if row is not None:
                    if xk > (row["updated"], row["device"]):
                        if self._edited(row["uid"]): conflicts += 1
                        self.db.execute("DELETE FROM notes WHERE id = ?", (row["id"],)); self.db.execute("INSERT OR REPLACE INTO tombstones(uid, deleted, device) VALUES (?, ?, ?)", (t["uid"], t["deleted"], t["device"])); applied += 1
                elif tomb is None: self.db.execute("INSERT INTO tombstones(uid, deleted, device) VALUES (?, ?, ?)", (t["uid"], t["deleted"], t["device"]))
                elif xk > (tomb[0], tomb[1]): self.db.execute("UPDATE tombstones SET deleted = ?, device = ? WHERE uid = ?", (t["deleted"], t["device"], t["uid"]))
            return {"applied": applied, "conflicts": conflicts}
        return self._tx(go)

    def sync_with(self, other):
        """Two-way merge with another NoteStore (a different device). Afterwards both hold the same notes and tombstones; a second call changes nothing.
        Returns {"pulled": changes made here, "pushed": changes made on the other store, "conflicts": discarded concurrent edits}."""
        if not isinstance(other, NoteStore): raise ValidationError("can only sync with another NoteStore")
        mine, theirs = self.snapshot(), other.snapshot(); a = self.apply_snapshot(theirs); b = other.apply_snapshot(mine)
        uids = [n["uid"] for n in mine["notes"] + theirs["notes"]]; self._mark_synced(uids); other._mark_synced(uids)
        return {"pulled": a["applied"], "pushed": b["applied"], "conflicts": a["conflicts"] + b["conflicts"]}

    def export_json(self, path):
        """Write all notes in the stage 2 JSON file format (for other tools) atomically; returns the number of notes."""
        with self._lock:
            notes = self.list(); seq = self.db.execute("SELECT seq FROM sqlite_sequence WHERE name = 'notes'").fetchone()
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".export-")
        with os.fdopen(fd, "w") as f: json.dump({"next_id": (seq[0] if seq else 0) + 1, "notes": [{k: v for k, v in n.items() if k not in ("owner", "reminder", "uid", "device")} for n in notes]}, f)
        os.replace(tmp, path); return len(notes)

    def close(self): self.db.close()

def sync_remote(store, base_url, timeout=30):
    """Two-way merge with a notes server (make_server without auth) over HTTP; same report as NoteStore.sync_with."""
    import urllib.request, urllib.error
    def call(method, path, body=None):
        req = urllib.request.Request(base_url.rstrip("/") + path, method=method, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())
        except urllib.error.HTTPError as e: raise StoreError(f"sync failed: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e: raise StoreError(f"sync failed: {e}") from e
    mine = store.snapshot(); theirs = call("GET", "/sync/snapshot"); a = store.apply_snapshot(theirs); b = call("POST", "/sync/apply", mine)
    store._mark_synced([n["uid"] for n in mine["notes"] + theirs["notes"]])
    return {"pulled": a["applied"], "pushed": b["applied"], "conflicts": a["conflicts"] + b["conflicts"]}

# ---------------- background worker ----------------
class Worker:
    """Runs the periodic jobs of the application: firing due reminders and deleting expired tokens."""
    def __init__(self, store, interval=1.0, clock=None):
        self.store, self.interval, self.clock = store, interval, clock
        self.runs, self.fired_total, self.last_report, self.last_error, self._stop, self._thread = 0, 0, None, None, threading.Event(), None

    def run_once(self, now=None):
        now = now or (self.clock() if self.clock else None); report = {"fired": self.store.fire_due(now), "tokens_purged": self.store.purge_tokens(now)}
        self.runs += 1; self.fired_total += len(report["fired"]); self.last_report = report; return report

    def start(self):
        if self._thread and self._thread.is_alive(): return self
        self._stop.clear()
        def loop():
            while not self._stop.is_set():
                try: self.run_once()
                except Exception as e: self.last_error = f"{type(e).__name__}: {e}"
                self._stop.wait(self.interval)
        self._thread = threading.Thread(target=loop, daemon=True); self._thread.start(); return self

    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(30); self._thread = None

# ---------------- HTTP API ----------------
MAX_BODY = 1_048_576

class HttpError(Exception):
    def __init__(self, status, error, headers=None): self.status, self.error, self.headers = status, error, headers or {}

class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, store, host, port, auth):
        self.store, self.auth, self._thread = store, auth, None; super().__init__((host, port), Handler); self.port = self.server_address[1]
    def start(self): self._thread = threading.Thread(target=self.serve_forever, daemon=True); self._thread.start(); return self
    def stop(self):
        if self._thread: self.shutdown(); self._thread.join(10); self._thread = None
        self.server_close()
    def __enter__(self): return self.start()
    def __exit__(self, *a): self.stop()

def make_server(store, host="127.0.0.1", port=0, auth=False): return Server(store, host, port, auth)

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def send(self, status, body=None, headers=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.end_headers()
        if data: self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY: self.close_connection = True; raise HttpError(413, "request too large")
        raw = self.rfile.read(n) if n else b""
        try: d = json.loads(raw.decode()) if raw else None
        except (ValueError, UnicodeDecodeError): raise HttpError(400, "invalid JSON")
        if not isinstance(d, dict): raise HttpError(400, "body must be a JSON object")
        return d

    ROUTES = [("GET", r"/health", "health"), ("GET", r"/notes", "list"), ("POST", r"/notes", "create"), ("GET", r"/notes/(\d+)", "get"), ("PUT", r"/notes/(\d+)", "put"), ("DELETE", r"/notes/(\d+)", "delete"), ("GET", r"/search", "search"), ("PUT", r"/notes/(\d+)/reminder", "remind"), ("DELETE", r"/notes/(\d+)/reminder", "unremind"), ("GET", r"/notifications", "notifications"), ("POST", r"/notifications/read", "read")]
    SYNC_ROUTES = [("GET", r"/sync/snapshot", "snapshot"), ("POST", r"/sync/apply", "apply")]
    AUTH_ROUTES = [("POST", r"/register", "register"), ("POST", r"/login", "login"), ("POST", r"/logout", "logout"), ("GET", r"/notes/(\d+)/shares", "shares"), ("POST", r"/notes/(\d+)/shares", "share"), ("DELETE", r"/notes/(\d+)/shares/([^/]+)", "unshare")]

    def handle_any(self):
        allowed = set()
        try:
            u = urlsplit(self.path); path = u.path.rstrip("/") or "/"; hit = None
            for m, pat, name in self.ROUTES + (self.AUTH_ROUTES if self.server.auth else self.SYNC_ROUTES):
                g = re.fullmatch(pat, path)
                if g:
                    allowed.add(m)
                    if m == self.command: hit = (name, [int(x) if x.isdigit() else unquote(x) for x in g.groups()])
            if not allowed: raise HttpError(404, "not found")
            if hit is None: raise HttpError(405, "method not allowed", {"Allow": ", ".join(sorted(allowed))})
            name, a = hit; s = self.server.store; actor = tok = None
            if self.server.auth and name not in ("health", "register", "login"):
                h = self.headers.get("Authorization", ""); tok = h[7:] if h.startswith("Bearer ") else None
                try: actor = s.user_for_token(tok)
                except AuthError: raise HttpError(401, "unauthorized", {"WWW-Authenticate": "Bearer"})
            qs = parse_qs(u.query, keep_blank_values=True)
            d = self.body() if self.command in ("POST", "PUT") and name != "logout" else None
            self.dispatch(name, a, qs, d, actor, tok)
        except HttpError as e: self.send(e.status, {"error": e.error}, e.headers)
        except AuthError as e: self.send(401, {"error": str(e)})
        except PermissionDenied as e: self.send(403, {"error": str(e)})
        except NotFound as e: self.send(404, {"error": f"no note with id {e.args[0]}" if isinstance(e.args[0], int) else f"not found: {e.args[0]}"})
        except ValidationError as e: self.send(422, {"error": str(e)})
        except StoreError as e: self.send(500, {"error": str(e)})

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = handle_any

    def paging(self, qs):
        def num(k, lo, hi, d):
            if k not in qs: return d
            v = qs[k][-1]
            if not re.fullmatch(r"[0-9]+", v) or not lo <= int(v) <= hi: raise HttpError(400, f"invalid {k}")
            return int(v)
        return num("limit", 1, 100, 20), num("offset", 0, 10**9, 0)

    def dispatch(self, name, a, qs, d, actor, tok):
        s = self.server.store
        if name == "health": return self.send(200, {"status": "ok"})
        if name == "register": return self.send(201, s.create_user(d.get("name"), d.get("password")))
        if name == "login": return self.send(200, {"token": s.login(d.get("name"), d.get("password"))})
        if name == "logout": s.logout(tok); return self.send(204)
        if name in ("list", "search"):
            limit, offset = self.paging(qs); items = s.search(qs.get("q", [""])[-1] if name == "search" else "", qs.get("tag", []), actor)
            return self.send(200, {"items": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset})
        if name == "create": n = s.add(d.get("title"), d.get("body", ""), d.get("tags", []), actor); return self.send(201, n, {"Location": f"/notes/{n['id']}"})
        if name == "get": return self.send(200, s.get(a[0], actor))
        if name == "put":
            if "title" not in d: raise ValidationError("title must not be empty")
            return self.send(200, s.update(a[0], d["title"], d.get("body", ""), d.get("tags", []), actor))
        if name == "snapshot": return self.send(200, s.snapshot())
        if name == "apply": return self.send(200, s.apply_snapshot(d))
        if name == "delete": s.delete(a[0], actor); return self.send(204)
        if name == "remind": return self.send(200, s.set_reminder(a[0], d.get("due"), actor))
        if name == "unremind": return self.send(200, s.clear_reminder(a[0], actor))
        if name == "notifications": items = s.notifications(actor, qs.get("unread", ["0"])[-1] in ("1", "true")); return self.send(200, {"items": items, "total": len(items)})
        if name == "read":
            if not isinstance(d.get("ids"), list): raise ValidationError("ids must be a list of notification ids")
            return self.send(200, {"marked": s.mark_read(d["ids"], actor)})
        if name == "shares": return self.send(200, {"items": s.shares(a[0], actor)})
        if name == "share": return self.send(200, {"items": s.share(a[0], d.get("user"), d.get("mode"), actor)})
        if name == "unshare": s.unshare(a[0], a[1], actor); return self.send(204)

def line(n): return f"{n['id']:>3}  {n['title']}" + (("  " + " ".join("#" + t for t in n["tags"])) if n.get("tags") else "")

def main(argv=None, out=None):
    out = out or sys.stdout; say = lambda s="": print(s, file=out)
    ap = argparse.ArgumentParser(prog="notes"); sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("add"); a.add_argument("title"); a.add_argument("--body", default=""); a.add_argument("--tag", action="append", default=[])
    l = sub.add_parser("list"); l.add_argument("--tag", action="append", default=[]); sub.add_parser("show").add_argument("id", type=int); sub.add_parser("delete").add_argument("id", type=int)
    e = sub.add_parser("edit"); e.add_argument("id", type=int); e.add_argument("--title"); e.add_argument("--body"); e.add_argument("--tag", action="append")
    q = sub.add_parser("search"); q.add_argument("words", nargs="+"); q.add_argument("--tag", action="append", default=[]); sub.add_parser("tags"); sub.add_parser("stats"); u = sub.add_parser("adduser"); u.add_argument("name"); u.add_argument("password"); rm = sub.add_parser("remind"); rm.add_argument("id", type=int); rm.add_argument("due", nargs="?"); rm.add_argument("--clear", action="store_true"); wk = sub.add_parser("worker"); wk.add_argument("--once", action="store_true"); wk.add_argument("--interval", type=float, default=1.0); nf = sub.add_parser("notifications"); nf.add_argument("--user"); nf.add_argument("--unread", action="store_true"); dv = sub.add_parser("device"); sy = sub.add_parser("sync"); sy.add_argument("target"); sh = sub.add_parser("share"); sh.add_argument("id", type=int); sh.add_argument("user"); sh.add_argument("mode"); sv = sub.add_parser("serve"); sv.add_argument("--auth", action="store_true"); sv.add_argument("--host", default="127.0.0.1"); sv.add_argument("--port", type=int, default=8080)
    try:
        ns = ap.parse_args(argv)
        if not ns.cmd: ap.print_usage(sys.stderr); return 2
        s = NoteStore(os.environ.get("NOTES_FILE") or "notes.json")
        if ns.cmd == "add": say(f"Added note {s.add(ns.title, ns.body, ns.tag)['id']}")
        elif ns.cmd == "list": ns_ = s.search("", ns.tag); say("\n".join(line(n) for n in ns_) if ns_ else "No notes.")
        elif ns.cmd == "search": ns_ = s.search(" ".join(ns.words), ns.tag); say("\n".join(line(n) for n in ns_) if ns_ else "No matches.")
        elif ns.cmd == "serve":
            srv = make_server(s, ns.host, ns.port, ns.auth); say(f"Serving on http://{ns.host}:{srv.port}"); out.flush()
            try: srv.serve_forever()
            except KeyboardInterrupt: pass
            finally: srv.server_close()
        elif ns.cmd == "remind":
            if ns.clear: s.clear_reminder(ns.id); say(f"Reminder cleared for note {ns.id}")
            elif ns.due is None: print("Error: give a time or --clear", file=sys.stderr); return 2
            else: s.set_reminder(ns.id, ns.due); say(f"Reminder set for note {ns.id} at {ns.due}")
        elif ns.cmd == "worker":
            w = Worker(s, ns.interval)
            if ns.once: r = w.run_once(); say(f"Fired {len(r['fired'])} reminder(s)")
            else:
                say("Worker running"); out.flush()
                try:
                    while True: r = w.run_once(); [say(f"Fired reminder of note {i}") for i in r["fired"]]; out.flush(); time.sleep(ns.interval)
                except KeyboardInterrupt: pass
        elif ns.cmd == "notifications":
            items = s.notifications(ns.user, ns.unread); say("\n".join(f"[{n['id']}] {n['ts']} {n['message']}" + ("" if n["read"] else " (new)") for n in items) if items else "No notifications.")
        elif ns.cmd == "device": say(s.device_id)
        elif ns.cmd == "sync":
            r = sync_remote(s, ns.target) if ns.target.startswith(("http://", "https://")) else s.sync_with(NoteStore(ns.target))
            say(f"Synced with {ns.target}: pulled {r['pulled']}, pushed {r['pushed']}, conflicts {r['conflicts']}")
        elif ns.cmd == "adduser": say(f"Added user {s.create_user(ns.name, ns.password)['name']}")
        elif ns.cmd == "share": s.share(ns.id, ns.user, ns.mode); say(f"Shared note {ns.id} with {ns.user} ({ns.mode})")
        elif ns.cmd == "stats": st = s.stats(); say(f"Notes: {st['notes']}"); say(f"Tags: {st['tags']}")
        elif ns.cmd == "tags": ts = s.tags(); say("\n".join(f"{t}: {c}" for t, c in ts) if ts else "No tags.")
        elif ns.cmd == "show": n = s.get(ns.id); say(n["title"]); say(n["body"])
        elif ns.cmd == "delete": say(f"Deleted note {s.delete(ns.id)['id']}")
        elif ns.cmd == "edit":
            if ns.title is None and ns.body is None and ns.tag is None: print("Error: nothing to edit", file=sys.stderr); return 2
            say(f"Updated note {s.update(ns.id, ns.title, ns.body, ns.tag)['id']}")
        return 0
    except NotFound as e: print(f"Error: no note with id {e.args[0]}" if isinstance(e.args[0], int) else f"Error: not found: {e.args[0]}", file=sys.stderr); return 1
    except PermissionDenied as e: print(f"Error: {e}", file=sys.stderr); return 3
    except AuthError as e: print(f"Error: {e}", file=sys.stderr); return 2
    except (ValidationError, StoreError) as e: print(f"Error: {e}", file=sys.stderr); return 2
    except SystemExit as e: return e.code if isinstance(e.code, int) else 2

if __name__ == "__main__": sys.exit(main())
