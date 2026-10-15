"""Synchronisation between devices."""
import json, re
from .errors import StoreError, ValidationError
from .validation import valid_body, valid_tags, valid_title

class SyncMixin:
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
            notes.append({"uid": n["uid"], "title": valid_title(n.get("title")), "body": valid_body(n.get("body")), "tags": valid_tags(n.get("tags")), "created": n["created"], "updated": n["updated"], "device": n["device"]})
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
        if not isinstance(other, SyncMixin): raise ValidationError("can only sync with another NoteStore")
        mine, theirs = self.snapshot(), other.snapshot(); a = self.apply_snapshot(theirs); b = other.apply_snapshot(mine)
        uids = [n["uid"] for n in mine["notes"] + theirs["notes"]]; self._mark_synced(uids); other._mark_synced(uids)
        return {"pulled": a["applied"], "pushed": b["applied"], "conflicts": a["conflicts"] + b["conflicts"]}


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
