"""Reminders and notifications."""
import datetime, re
from .errors import ValidationError

class RemindersMixin:
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
