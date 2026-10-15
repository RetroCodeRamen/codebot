"""Users, login tokens and sharing of notes."""
import datetime, hashlib, hmac, re, secrets
from .errors import AuthError, NotFound, ValidationError

class AccountsMixin:
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

    def purge_tokens(self, now=None):
        now = now or self.clock(); return self._tx(lambda: self.db.execute("DELETE FROM tokens WHERE expires <= ?", (now,)).rowcount)
