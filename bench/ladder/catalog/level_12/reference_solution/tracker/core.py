import csv, hashlib, hmac, io, json, os, re, secrets, sqlite3, threading, time
from contextlib import contextmanager
from . import search as S

class TrackerError(Exception): pass
class AuthError(TrackerError): pass
class PermissionDenied(TrackerError): pass
class NotFound(TrackerError): pass
class ValidationError(TrackerError, ValueError): pass
class Conflict(TrackerError): pass

SCHEMA = ["CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE, pw_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','member','viewer')), active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL)",
          "CREATE TABLE tokens(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), created TEXT NOT NULL, expires TEXT NOT NULL)",
          "CREATE TABLE projects(key TEXT PRIMARY KEY, name TEXT NOT NULL, public INTEGER NOT NULL DEFAULT 0, next_number INTEGER NOT NULL DEFAULT 1, archived INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL)",
          "CREATE TABLE members(project_key TEXT NOT NULL REFERENCES projects(key), user_id INTEGER NOT NULL REFERENCES users(id), role TEXT NOT NULL CHECK(role IN ('manager','developer','reporter')), PRIMARY KEY(project_key, user_id))",
          "CREATE TABLE issues(id INTEGER PRIMARY KEY AUTOINCREMENT, project_key TEXT NOT NULL REFERENCES projects(key), number INTEGER NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, priority TEXT NOT NULL, "
          "reporter_id INTEGER NOT NULL REFERENCES users(id), assignee_id INTEGER REFERENCES users(id), resolution TEXT, created TEXT NOT NULL, updated TEXT NOT NULL, closed TEXT, UNIQUE(project_key, number))",
          "CREATE TABLE comments(id INTEGER PRIMARY KEY AUTOINCREMENT, issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE, author_id INTEGER NOT NULL REFERENCES users(id), body TEXT NOT NULL, created TEXT NOT NULL, edited TEXT)",
          "CREATE TABLE labels(issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE, label TEXT NOT NULL, PRIMARY KEY(issue_id, label))",
          "CREATE TABLE audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT, action TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT, outcome TEXT NOT NULL, changes TEXT NOT NULL, reason TEXT)",
          "CREATE INDEX issues_project ON issues(project_key, status)", "CREATE INDEX audit_entity ON audit(entity, entity_id)"]
TRANSITIONS = {"open": {"in_progress", "resolved", "closed"}, "in_progress": {"open", "resolved", "closed"}, "resolved": {"open", "closed"}, "closed": {"open"}}
CAN = {"manager": {"view", "create_issue", "edit_issue", "edit_own_issue", "transition", "assign", "comment", "edit_own_comment", "delete_comment", "label", "manage_members", "export"},
       "developer": {"view", "create_issue", "edit_issue", "edit_own_issue", "transition", "assign", "comment", "edit_own_comment", "label"},
       "reporter": {"view", "create_issue", "edit_own_issue", "comment", "edit_own_comment"}}
KEY = re.compile(r"^[A-Z][A-Z0-9]{1,9}$"); LABEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$"); ISSUE = re.compile(r"^([A-Z][A-Z0-9]{1,9})-([1-9][0-9]*)$")
TOKEN_HOURS = 24

def _hash(password, salt=None):
    salt = salt or secrets.token_hex(8); return f"{salt}${hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 20000).hex()}"

def _check(password, stored):
    salt, _, _ = stored.partition("$"); return hmac.compare_digest(_hash(password, salt), stored)

def _plus_hours(ts, hours):
    import datetime; return (datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S") + datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")

class Tracker:
    def __init__(self, path=":memory:", clock=None):
        self.clock = clock or (lambda: time.strftime("%Y-%m-%dT%H:%M:%S")); self._lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False); self.db.execute("PRAGMA foreign_keys = ON")
        self.tx(lambda: None if self.db.execute("SELECT 1 FROM sqlite_master WHERE name = 'users'").fetchone() else [self.db.execute(s) for s in SCHEMA])

    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    def tx(self, fn):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try: out = fn(); self.db.execute("COMMIT"); return out
            except BaseException: self.db.execute("ROLLBACK"); raise

    # ---- audit ----
    def _audit(self, actor, action, entity, entity_id, changes=None, outcome="ok", reason=None):
        self.db.execute("INSERT INTO audit(ts, actor, action, entity, entity_id, outcome, changes, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (self.clock(), actor, action, entity, None if entity_id is None else str(entity_id), outcome, json.dumps(changes or {}), reason))

    def _denied(self, actor, action, entity, entity_id, reason):
        """Record a refusal in a transaction of its own (the caller's work was rolled back) and raise PermissionDenied."""
        self.tx(lambda: self._audit(actor, action, entity, entity_id, {}, "denied", reason)); raise PermissionDenied(reason)

    def audit_entries(self, actor, action=None, entity=None, entity_id=None, who=None, outcome=None, since=None, limit=None):
        with self._lock:
            user = self._active(actor)
            if user["role"] != "admin": raise PermissionDenied("only admins may read the audit log")
            q, a = "SELECT seq, ts, actor, action, entity, entity_id, outcome, changes, reason FROM audit WHERE 1=1", []
            for col, v in (("action", action), ("entity", entity), ("entity_id", None if entity_id is None else str(entity_id)), ("actor", who), ("outcome", outcome)):
                if v is not None: q += f" AND {col} = ?"; a.append(v)
            if since is not None: q += " AND ts >= ?"; a.append(since)
            rows = self.db.execute(q + " ORDER BY seq", a).fetchall()
            out = [{"seq": r[0], "ts": r[1], "actor": r[2], "action": r[3], "entity": r[4], "entity_id": r[5], "outcome": r[6], "changes": json.loads(r[7]), "reason": r[8]} for r in rows]
            return out[-limit:] if limit else out

    # ---- users and authentication ----
    def _user(self, name):
        r = self.db.execute("SELECT id, name, role, active, created FROM users WHERE name = ?", (name,)).fetchone() if isinstance(name, str) else None
        if r is None: raise NotFound(f"no user '{name}'")
        return {"id": r[0], "name": r[1], "role": r[2], "active": bool(r[3]), "created": r[4]}

    def _active(self, actor):
        u = self._user(actor)
        if not u["active"]: raise PermissionDenied(f"{u['name']} is disabled")
        return u

    def create_user(self, name, password, role="member", actor=None):
        def go():
            n = self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if n or actor is not None:
                if actor is None or self._user(actor)["role"] != "admin" or not self._user(actor)["active"]: return ("denied", f"{actor or 'nobody'} may not create users")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{1,29}", name): raise ValidationError("user names have 2-30 characters: letters, digits, '.', '_' and '-', starting with a letter")
            if not isinstance(password, str) or len(password) < 8: raise ValidationError("the password must have at least 8 characters")
            if role not in ("admin", "member", "viewer"): raise ValidationError(f"invalid role '{role}'")
            if n == 0 and role != "admin": raise ValidationError("the first user must be an admin")
            if self.db.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone(): raise Conflict(f"user '{name}' already exists")
            self.db.execute("INSERT INTO users(name, pw_hash, role, created) VALUES (?, ?, ?, ?)", (name, _hash(password), role, self.clock()))
            self._audit(actor, "user.create", "user", name, {"name": [None, name], "role": [None, role]}); return ("ok", self._user(name))
        r = self.tx(go)
        if r[0] == "denied": self._denied(actor, "user.create", "user", None, r[1])
        return r[1]

    def get_user(self, name): return self._user(name)

    def list_users(self, actor):
        self._active(actor); return [self._user(r[0]) for r in self.db.execute("SELECT name FROM users ORDER BY id").fetchall()]

    def set_active(self, name, active, actor):
        def go():
            a = self._user(actor)
            if a["role"] != "admin" or not a["active"]: return ("denied", f"{actor} may not change users")
            u = self._user(name)
            if u["active"] != bool(active):
                self.db.execute("UPDATE users SET active = ? WHERE id = ?", (int(bool(active)), u["id"]))
                if not active: self.db.execute("DELETE FROM tokens WHERE user_id = ?", (u["id"],))
                self._audit(a["name"], "user.active", "user", u["name"], {"active": [u["active"], bool(active)]})
            return ("ok", self._user(name))
        r = self.tx(go)
        if r[0] == "denied": self._denied(actor, "user.active", "user", name, r[1])
        return r[1]

    def login(self, name, password):
        """A token valid for 24 hours. A failed attempt is audited as outcome 'denied'."""
        def go():
            r = self.db.execute("SELECT id, name, pw_hash, active FROM users WHERE name = ?", (name,)).fetchone() if isinstance(name, str) else None
            if r is None or not r[3] or not isinstance(password, str) or not _check(password, r[2]):
                self._audit(name if isinstance(name, str) else None, "auth.login", "user", name if isinstance(name, str) else None, {}, "denied", "invalid credentials"); return None
            tok = secrets.token_hex(16); now = self.clock()
            self.db.execute("INSERT INTO tokens(token, user_id, created, expires) VALUES (?, ?, ?, ?)", (tok, r[0], now, _plus_hours(now, TOKEN_HOURS)))
            self._audit(r[1], "auth.login", "user", r[1], {}); return tok
        tok = self.tx(go)
        if tok is None: raise AuthError("invalid credentials")
        return tok

    def authenticate_token(self, token):
        with self._lock:
            r = self.db.execute("SELECT u.name, t.expires FROM tokens t JOIN users u ON u.id = t.user_id WHERE t.token = ? AND u.active = 1", (token,)).fetchone() if isinstance(token, str) else None
            if r is None or r[1] <= self.clock(): raise AuthError("invalid or expired token")
            return self._user(r[0])

    def logout(self, token): self.tx(lambda: self.db.execute("DELETE FROM tokens WHERE token = ?", (token,)))

    # ---- projects ----
    def _project(self, key):
        r = self.db.execute("SELECT key, name, public, next_number, archived, created FROM projects WHERE key = ?", (key,)).fetchone() if isinstance(key, str) else None
        if r is None: raise NotFound(f"no project '{key}'")
        return {"key": r[0], "name": r[1], "public": bool(r[2]), "archived": bool(r[4]), "created": r[5], "next_number": r[3]}

    def _member_role(self, key, user):
        r = self.db.execute("SELECT role FROM members WHERE project_key = ? AND user_id = ?", (key, user["id"])).fetchone(); return r[0] if r else None

    def _can(self, user, key, action):
        """Whether the user may perform `action` in the project. Admins may do everything except write to an archived project; global viewers never write."""
        p = self._project(key); role = self._member_role(key, user)
        if not user["active"]: return False
        if action not in ("view", "export") and (p["archived"] or user["role"] == "viewer"): return False
        if user["role"] == "admin": return True
        if action == "view": return p["public"] or role is not None
        return role is not None and action in CAN[role]

    def _need(self, actor, key, action):
        """(user, None) when allowed, (user, reason) when the user can see the project but may not do this; NotFound when the project is invisible to the user."""
        user = self._active(actor); self._project(key)
        if not self._can(user, key, "view"): raise NotFound(f"no project '{key}'")
        if not self._can(user, key, action): return user, f"{actor} may not {action.replace('_', ' ')} in {key}"
        return user, None

    def _visible_keys(self, user):
        if user["role"] == "admin": return None
        rows = self.db.execute("SELECT key FROM projects WHERE public = 1 UNION SELECT project_key FROM members WHERE user_id = ?", (user["id"],)).fetchall(); return sorted(r[0] for r in rows)

    def create_project(self, key, name, actor, public=False):
        def go():
            u = self._active(actor)
            if u["role"] != "admin" or not u["active"]: return ("denied", f"{actor} may not create projects")
            if not isinstance(key, str) or not KEY.match(key): raise ValidationError("a project key is 2-10 characters: capital letters and digits, starting with a letter")
            if not isinstance(name, str) or not name.strip() or len(name) > 100: raise ValidationError("a project name is 1-100 characters")
            if self.db.execute("SELECT 1 FROM projects WHERE key = ?", (key,)).fetchone(): raise Conflict(f"project {key} already exists")
            self.db.execute("INSERT INTO projects(key, name, public, created) VALUES (?, ?, ?, ?)", (key, name.strip(), int(bool(public)), self.clock()))
            self._audit(u["name"], "project.create", "project", key, {"name": [None, name.strip()], "public": [None, bool(public)]}); return ("ok", self._public_project(key))
        r = self.tx(go)
        if r[0] == "denied": self._denied(actor, "project.create", "project", key if isinstance(key, str) else None, r[1])
        return r[1]

    def _public_project(self, key):
        p = self._project(key); p.pop("next_number"); return p

    def get_project(self, key, actor):
        with self._lock:
            u = self._active(actor); p = self._project(key)
            if not self._can(u, key, "view"): raise NotFound(f"no project '{key}'")
            return self._public_project(key)

    def list_projects(self, actor):
        with self._lock:
            u = self._active(actor); keys = self._visible_keys(u)
            rows = self.db.execute("SELECT key FROM projects ORDER BY key").fetchall()
            return [self._public_project(r[0]) for r in rows if keys is None or r[0] in keys]

    def members(self, key, actor):
        with self._lock:
            u = self._active(actor); self.get_project(key, actor)
            return [{"user": r[0], "role": r[1]} for r in self.db.execute("SELECT u.name, m.role FROM members m JOIN users u ON u.id = m.user_id WHERE m.project_key = ? ORDER BY u.name COLLATE NOCASE", (key,))]

    def add_member(self, key, user_name, role, actor):
        def go():
            u, why = self._need(actor, key, "manage_members")
            if why: return ("denied", why)
            if role not in CAN: raise ValidationError(f"invalid project role '{role}'")
            m = self._user(user_name); old = self._member_role(key, m)
            self.db.execute("INSERT INTO members(project_key, user_id, role) VALUES (?, ?, ?) ON CONFLICT(project_key, user_id) DO UPDATE SET role = excluded.role", (key, m["id"], role))
            if old != role: self._audit(u["name"], "project.member_add", "project", key, {"member": [None, m["name"]], "role": [old, role]})
            return ("ok", {"user": m["name"], "role": role})
        r = self.tx(go)
        if r[0] == "denied": self._denied(actor, "project.member_add", "project", key, r[1])
        return r[1]

    def remove_member(self, key, user_name, actor):
        def go():
            u, why = self._need(actor, key, "manage_members")
            if why: return ("denied", why)
            m = self._user(user_name); old = self._member_role(key, m)
            if old is None: raise NotFound(f"{user_name} is not a member of {key}")
            self.db.execute("DELETE FROM members WHERE project_key = ? AND user_id = ?", (key, m["id"])); self._audit(u["name"], "project.member_remove", "project", key, {"member": [m["name"], None], "role": [old, None]}); return ("ok", None)
        r = self.tx(go)
        if r[0] == "denied": self._denied(actor, "project.member_remove", "project", key, r[1])

    def archive_project(self, key, actor):
        def go():
            u = self._active(actor); self._project(key)
            if not self._can(u, key, "view"): raise NotFound(f"no project '{key}'")
            if u["role"] != "admin": return ("denied", f"{actor} may not archive projects")
            if not self._project(key)["archived"]: self.db.execute("UPDATE projects SET archived = 1 WHERE key = ?", (key,)); self._audit(u["name"], "project.archive", "project", key, {"archived": [False, True]})
            return ("ok", self._public_project(key))
        r = self.tx(go)
        if r[0] == "denied": self._denied(actor, "project.archive", "project", key, r[1])
        return r[1]

    # ---- issues ----
    def _issue_row(self, issue_key):
        m = ISSUE.match(issue_key) if isinstance(issue_key, str) else None
        r = self.db.execute("SELECT id, project_key, number, title, description, status, priority, reporter_id, assignee_id, resolution, created, updated, closed FROM issues WHERE project_key = ? AND number = ?", (m.group(1), int(m.group(2)))).fetchone() if m else None
        if r is None: raise NotFound(f"no issue '{issue_key}'")
        return r

    def _issue_dict(self, r, with_comments=False):
        names = {x[0]: x[1] for x in self.db.execute("SELECT id, name FROM users")}
        d = {"key": f"{r[1]}-{r[2]}", "project": r[1], "number": r[2], "title": r[3], "description": r[4], "status": r[5], "priority": r[6], "reporter": names.get(r[7]), "assignee": names.get(r[8]),
             "resolution": r[9], "created": r[10], "updated": r[11], "closed": r[12], "labels": sorted(x[0] for x in self.db.execute("SELECT label FROM labels WHERE issue_id = ?", (r[0],)))}
        if with_comments: d["comments"] = [{"id": c[0], "author": names.get(c[1]), "body": c[2], "created": c[3], "edited": c[4]} for c in self.db.execute("SELECT id, author_id, body, created, edited FROM comments WHERE issue_id = ? ORDER BY id", (r[0],))]
        return d

    def _check_text(self, v, name, maximum, allow_empty=False):
        if not isinstance(v, str) or (not allow_empty and not v.strip()) or len(v) > maximum: raise ValidationError(f"{name} must be a {'' if allow_empty else 'non-empty '}string of at most {maximum} characters")
        return v.strip() if not allow_empty else v

    def _label(self, label):
        if not isinstance(label, str): raise ValidationError("a label must be a string")
        label = label.strip().lower()
        if not LABEL.match(label): raise ValidationError(f"invalid label '{label}'")
        return label

    def create_issue(self, project, title, description="", actor=None, priority="medium", assignee=None, labels=()):
        def go():
            u, why = self._need(actor, project, "create_issue")
            if why: return ("denied", why)
            t = self._check_text(title, "title", 200); d = self._check_text(description, "description", 20000, True)
            if priority not in S.PRIORITIES: raise ValidationError(f"invalid priority '{priority}'")
            aid = None
            if assignee is not None:
                if not self._can(u, project, "assign"): return ("denied", f"{actor} may not assign in {project}")
                a = self._user(assignee); aid = a["id"]
                if not self._can(a, project, "comment") and a["role"] != "admin": raise ValidationError(f"{assignee} is not a member of {project}")
            labs = sorted({self._label(l) for l in labels})
            if labs and not self._can(u, project, "label"): return ("denied", f"{actor} may not label in {project}")
            num = self._project(project)["next_number"]; now = self.clock()
            iid = self.db.execute("INSERT INTO issues(project_key, number, title, description, status, priority, reporter_id, assignee_id, created, updated) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)", (project, num, t, d, priority, u["id"], aid, now, now)).lastrowid
            self.db.execute("UPDATE projects SET next_number = ? WHERE key = ?", (num + 1, project))
            for l in labs: self.db.execute("INSERT INTO labels(issue_id, label) VALUES (?, ?)", (iid, l))
            self._audit(u["name"], "issue.create", "issue", f"{project}-{num}", {"title": [None, t], "priority": [None, priority], "assignee": [None, assignee], "labels": [[], labs]}); return ("ok", self._issue_dict(self._issue_row(f"{project}-{num}")))
        r = self.tx(go)
        if r[0] == "denied": self._denied(actor, "issue.create", "issue", None, r[1])
        return r[1]

    def get_issue(self, issue_key, actor):
        with self._lock:
            u = self._active(actor); r = self._issue_row(issue_key)
            if not self._can(u, r[1], "view"): raise NotFound(f"no issue '{issue_key}'")
            return self._issue_dict(r, True)

    def update_issue(self, issue_key, actor, **fields):
        def go():
            r = self._issue_row(issue_key); u = self._active(actor); project = r[1]
            if not self._can(u, project, "view"): raise NotFound(f"no issue '{issue_key}'")
            unknown = set(fields) - {"title", "description", "priority", "assignee"}
            if unknown: raise ValidationError(f"unknown field(s): {', '.join(sorted(unknown))}")
            own = r[7] == u["id"]
            def allowed(f):
                if f == "assignee": return self._can(u, project, "assign")
                if f == "priority": return self._can(u, project, "edit_issue")
                return self._can(u, project, "edit_issue") or (own and self._can(u, project, "edit_own_issue"))
            for f in fields:
                if not allowed(f): return ("denied", f"{actor} may not change the {f} of {issue_key}")
            sets, changes, assign_change = {}, {}, None
            if "title" in fields: sets["title"] = self._check_text(fields["title"], "title", 200)
            if "description" in fields: sets["description"] = self._check_text(fields["description"], "description", 20000, True)
            if "priority" in fields:
                if fields["priority"] not in S.PRIORITIES: raise ValidationError(f"invalid priority '{fields['priority']}'")
                sets["priority"] = fields["priority"]
            old = {"title": r[3], "description": r[4], "priority": r[6]}
            for k, v in sets.items():
                if old[k] != v: changes[k] = [old[k], v]
            if "assignee" in fields:
                names = {x[0]: x[1] for x in self.db.execute("SELECT id, name FROM users")}; old_a = names.get(r[8])
                if fields["assignee"] is None: new_id, new_name = None, None
                else:
                    a = self._user(fields["assignee"]); new_id, new_name = a["id"], a["name"]
                    if not self._can(a, project, "comment") and a["role"] != "admin": raise ValidationError(f"{fields['assignee']} is not a member of {project}")
                if old_a != new_name: assign_change = {"assignee": [old_a, new_name]}; sets["assignee_id"] = new_id
            if not changes and not assign_change: return ("ok", self._issue_dict(r))
            now = self.clock(); sets["updated"] = now
            self.db.execute(f"UPDATE issues SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", [*sets.values(), r[0]])
            if changes: self._audit(u["name"], "issue.update", "issue", issue_key, changes)
            if assign_change: self._audit(u["name"], "issue.assign", "issue", issue_key, assign_change)
            return ("ok", self._issue_dict(self._issue_row(issue_key)))
        r = self.tx(go)
        if r[0] == "denied": self._denied(actor, "issue.update", "issue", issue_key, r[1])
        return r[1]

    def transition(self, issue_key, status, actor, resolution=None):
        def go():
            r = self._issue_row(issue_key); u = self._active(actor)
            if not self._can(u, r[1], "view"): raise NotFound(f"no issue '{issue_key}'")
            if not self._can(u, r[1], "transition"): return ("denied", f"{actor} may not change the status of {issue_key}")
            if status not in TRANSITIONS: raise ValidationError(f"invalid status '{status}'")
            if status not in TRANSITIONS[r[5]]: raise ValidationError(f"{issue_key} cannot go from {r[5]} to {status}")
            if status == "resolved" and not (isinstance(resolution, str) and resolution.strip()): raise ValidationError("a resolution text is required to resolve an issue")
            now = self.clock(); res = resolution.strip() if status == "resolved" else (None if status == "open" else r[9])
            self.db.execute("UPDATE issues SET status = ?, resolution = ?, closed = ?, updated = ? WHERE id = ?", (status, res, now if status == "closed" else None, now, r[0]))
            ch = {"status": [r[5], status]}
            if res != r[9]: ch["resolution"] = [r[9], res]
            self._audit(u["name"], "issue.transition", "issue", issue_key, ch); return ("ok", self._issue_dict(self._issue_row(issue_key)))
        r = self.tx(go)
        if r[0] == "denied": self._denied(actor, "issue.transition", "issue", issue_key, r[1])
        return r[1]

    def add_label(self, issue_key, label, actor): return self._label_change(issue_key, label, actor, True)
    def remove_label(self, issue_key, label, actor): return self._label_change(issue_key, label, actor, False)

    def _label_change(self, issue_key, label, actor, add):
        action = "label.add" if add else "label.remove"
        def go():
            r = self._issue_row(issue_key); u = self._active(actor)
            if not self._can(u, r[1], "view"): raise NotFound(f"no issue '{issue_key}'")
            if not self._can(u, r[1], "label"): return ("denied", f"{actor} may not change labels of {issue_key}")
            label_ = self._label(label); cur = sorted(x[0] for x in self.db.execute("SELECT label FROM labels WHERE issue_id = ?", (r[0],)))
            if add and label_ not in cur:
                if len(cur) >= 10: raise ValidationError("an issue can have at most 10 labels")
                self.db.execute("INSERT INTO labels(issue_id, label) VALUES (?, ?)", (r[0], label_)); self.db.execute("UPDATE issues SET updated = ? WHERE id = ?", (self.clock(), r[0])); self._audit(u["name"], action, "issue", issue_key, {"labels": [cur, sorted(cur + [label_])]})
            if not add:
                if label_ not in cur: raise NotFound(f"{issue_key} has no label '{label_}'")
                self.db.execute("DELETE FROM labels WHERE issue_id = ? AND label = ?", (r[0], label_)); self.db.execute("UPDATE issues SET updated = ? WHERE id = ?", (self.clock(), r[0])); self._audit(u["name"], action, "issue", issue_key, {"labels": [cur, [x for x in cur if x != label_]]})
            return ("ok", self._issue_dict(self._issue_row(issue_key)))
        r = self.tx(go)
        if r[0] == "denied": self._denied(actor, action, "issue", issue_key, r[1])
        return r[1]

    # ---- comments ----
    def add_comment(self, issue_key, body, actor):
        def go():
            r = self._issue_row(issue_key); u = self._active(actor)
            if not self._can(u, r[1], "view"): raise NotFound(f"no issue '{issue_key}'")
            if not self._can(u, r[1], "comment"): return ("denied", f"{actor} may not comment on {issue_key}")
            b = self._check_text(body, "comment", 20000); now = self.clock()
            cid = self.db.execute("INSERT INTO comments(issue_id, author_id, body, created) VALUES (?, ?, ?, ?)", (r[0], u["id"], b, now)).lastrowid
            self.db.execute("UPDATE issues SET updated = ? WHERE id = ?", (now, r[0])); self._audit(u["name"], "comment.add", "issue", issue_key, {"comment_id": [None, cid]}); return ("ok", {"id": cid, "author": u["name"], "body": b, "created": now, "edited": None})
        r = self.tx(go)
        if r[0] == "denied": self._denied(actor, "comment.add", "issue", issue_key, r[1])
        return r[1]

    def _comment(self, comment_id):
        r = self.db.execute("SELECT c.id, c.issue_id, c.author_id, c.body, i.project_key, i.number FROM comments c JOIN issues i ON i.id = c.issue_id WHERE c.id = ?", (comment_id,)).fetchone()
        if r is None: raise NotFound(f"no comment {comment_id}")
        return r

    def edit_comment(self, comment_id, body, actor):
        def go():
            c = self._comment(comment_id); u = self._active(actor)
            if not self._can(u, c[4], "view"): raise NotFound(f"no comment {comment_id}")
            if not (c[2] == u["id"] and self._can(u, c[4], "edit_own_comment")): return ("denied", f"{actor} may not edit comment {comment_id}")
            b = self._check_text(body, "comment", 20000); now = self.clock()
            if b != c[3]: self.db.execute("UPDATE comments SET body = ?, edited = ? WHERE id = ?", (b, now, comment_id)); self._audit(u["name"], "comment.edit", "issue", f"{c[4]}-{c[5]}", {"comment_id": [comment_id, comment_id], "body": [c[3], b]})
            return ("ok", {"id": comment_id, "body": b})
        r = self.tx(go)
        if r[0] == "denied": self._denied(actor, "comment.edit", "comment", comment_id, r[1])
        return r[1]

    def delete_comment(self, comment_id, actor):
        def go():
            c = self._comment(comment_id); u = self._active(actor)
            if not self._can(u, c[4], "view"): raise NotFound(f"no comment {comment_id}")
            if not (self._can(u, c[4], "delete_comment") or (c[2] == u["id"] and self._can(u, c[4], "edit_own_comment"))): return ("denied", f"{actor} may not delete comment {comment_id}")
            self.db.execute("DELETE FROM comments WHERE id = ?", (comment_id,)); self._audit(u["name"], "comment.delete", "issue", f"{c[4]}-{c[5]}", {"comment_id": [comment_id, None], "body": [c[3], None]}); return ("ok", None)
        r = self.tx(go)
        if r[0] == "denied": self._denied(actor, "comment.delete", "comment", comment_id, r[1])

    # ---- search, stats ----
    def search(self, query="", actor=None, limit=20, offset=0, sort="-updated"):
        with self._lock:
            u = self._active(actor)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100: raise ValidationError("limit must be an integer from 1 to 100")
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0: raise ValidationError("offset must be a non-negative integer")
            try: filters, words = S.parse(query); order = S.order_by(sort)
            except S.QueryError as e: raise ValidationError(str(e))
            where, args = S.build_sql(filters, words, self._visible_keys(u))
            total = self.db.execute(f"SELECT COUNT(*) FROM issues i WHERE {where}", args).fetchone()[0]
            rows = self.db.execute(f"SELECT i.id, i.project_key, i.number, i.title, i.description, i.status, i.priority, i.reporter_id, i.assignee_id, i.resolution, i.created, i.updated, i.closed FROM issues i WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
            return {"items": [self._issue_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    def project_stats(self, key, actor):
        with self._lock:
            self.get_project(key, actor); out = {"project": key, "total": 0, "by_status": {}, "by_priority": {}, "by_assignee": {}, "by_label": {}}
            for status, priority, assignee in self.db.execute("SELECT i.status, i.priority, COALESCE(u.name, '-') FROM issues i LEFT JOIN users u ON u.id = i.assignee_id WHERE i.project_key = ?", (key,)):
                out["total"] += 1
                for k, v in (("by_status", status), ("by_priority", priority), ("by_assignee", assignee)): out[k][v] = out[k].get(v, 0) + 1
            for label, n in self.db.execute("SELECT l.label, COUNT(*) FROM labels l JOIN issues i ON i.id = l.issue_id WHERE i.project_key = ? GROUP BY l.label ORDER BY l.label", (key,)): out["by_label"][label] = n
            return {k: (dict(sorted(v.items())) if isinstance(v, dict) else v) for k, v in out.items()}

    # ---- export and import ----
    def export_project(self, key, actor):
        with self._lock:
            u = self._active(actor); p = self.get_project(key, actor)
            if not self._can(u, key, "export") and u["role"] != "admin": raise PermissionDenied(f"{actor} may not export {key}")
            names = {x[0]: x[1] for x in self.db.execute("SELECT id, name FROM users")}; issues = []
            for r in self.db.execute("SELECT id, project_key, number, title, description, status, priority, reporter_id, assignee_id, resolution, created, updated, closed FROM issues WHERE project_key = ? ORDER BY number", (key,)).fetchall():
                d = self._issue_dict(r, True); issues.append({k: d[k] for k in ("number", "title", "description", "status", "priority", "reporter", "assignee", "resolution", "created", "updated", "closed", "labels")} | {"comments": [{k: c[k] for k in ("author", "body", "created", "edited")} for c in d["comments"]]})
            return {"format": "tracker-export", "version": 1, "project": {k: p[k] for k in ("key", "name", "public", "archived", "created")}, "members": self.members(key, actor), "issues": issues}

    def import_project(self, data, actor):
        def go():
            u = self._active(actor)
            if u["role"] != "admin" or not u["active"]: return ("denied", f"{actor} may not import projects")
            if not isinstance(data, dict) or data.get("format") != "tracker-export" or data.get("version") != 1 or not isinstance(data.get("project"), dict) or not isinstance(data.get("issues"), list): raise ValidationError("not a tracker export")
            p = data["project"]
            if not isinstance(p.get("key"), str) or not KEY.match(p["key"]): raise ValidationError("the export has an invalid project key")
            if self.db.execute("SELECT 1 FROM projects WHERE key = ?", (p["key"],)).fetchone(): raise Conflict(f"project {p['key']} already exists")
            users = {x[1].lower(): x[0] for x in self.db.execute("SELECT id, name FROM users")}
            need = {m["user"] for m in data.get("members", [])} | {n for i in data["issues"] for n in [i.get("reporter"), i.get("assignee")] + [c.get("author") for c in i.get("comments", [])] if n}
            missing = sorted(n for n in need if n.lower() not in users)
            if missing: raise ValidationError(f"unknown user(s) in the export: {', '.join(missing)}")
            nums = [i.get("number") for i in data["issues"]]
            if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in nums) or len(set(nums)) != len(nums): raise ValidationError("issue numbers must be unique positive integers")
            self.db.execute("INSERT INTO projects(key, name, public, next_number, archived, created) VALUES (?, ?, ?, ?, ?, ?)", (p["key"], p["name"], int(bool(p.get("public"))), max(nums, default=0) + 1, int(bool(p.get("archived"))), p.get("created") or self.clock()))
            for m in data.get("members", []): self.db.execute("INSERT INTO members(project_key, user_id, role) VALUES (?, ?, ?)", (p["key"], users[m["user"].lower()], m["role"]))
            for i in data["issues"]:
                iid = self.db.execute("INSERT INTO issues(project_key, number, title, description, status, priority, reporter_id, assignee_id, resolution, created, updated, closed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                      (p["key"], i["number"], i["title"], i.get("description", ""), i["status"], i["priority"], users[i["reporter"].lower()], users[i["assignee"].lower()] if i.get("assignee") else None, i.get("resolution"), i["created"], i["updated"], i.get("closed"))).lastrowid
                for l in i.get("labels", []): self.db.execute("INSERT INTO labels(issue_id, label) VALUES (?, ?)", (iid, l))
                for c in i.get("comments", []): self.db.execute("INSERT INTO comments(issue_id, author_id, body, created, edited) VALUES (?, ?, ?, ?, ?)", (iid, users[c["author"].lower()], c["body"], c["created"], c.get("edited")))
            self._audit(u["name"], "project.import", "project", p["key"], {"issues": [0, len(data["issues"])]}); return ("ok", self._public_project(p["key"]))
        try: r = self.tx(go)
        except (KeyError, TypeError, sqlite3.Error) as e: raise ValidationError(f"the export is damaged: {e!r}")
        if r[0] == "denied": self._denied(actor, "project.import", "project", None, r[1])
        return r[1]

    def export_csv(self, key, actor, path):
        data = self.export_project(key, actor); buf = io.StringIO(); w = csv.writer(buf); w.writerow(["key", "title", "status", "priority", "reporter", "assignee", "labels", "created", "updated"])
        for i in data["issues"]: w.writerow([f"{key}-{i['number']}", i["title"], i["status"], i["priority"], i["reporter"], i["assignee"] or "", "|".join(i["labels"]), i["created"], i["updated"]])
        d = os.path.dirname(os.path.abspath(path)); import tempfile; fd, tmp = tempfile.mkstemp(dir=d, prefix=".export-")
        with os.fdopen(fd, "w", newline="") as f: f.write(buf.getvalue())
        os.replace(tmp, path); return len(data["issues"])
