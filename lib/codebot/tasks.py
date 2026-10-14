"""tasks.json: the structured task list. PLAN.md is rendered from it; the model never edits either."""
import hashlib, json, os, fcntl, shutil, tempfile, re
from contextlib import contextmanager
from .config import now

STATUSES = ("pending", "running", "done", "blocked")

def new_task(tid, title, check=None, files=None, size=None, deps=None, parent=None):
    return {"id": tid, "title": title, "check": check, "files": files or [], "size": size or "M",
            "status": "pending", "deps": deps or [], "parent": parent, "attempts": 0, "ladder": 1, "rung_tries": 0,
            "attempts_base": 0, "blocked_question": None, "history": []}

class TaskStore:
    def __init__(self, proj):
        self.proj = proj; self.path = proj.p("tasks.json"); self.lockpath = proj.p("tasks.json.lock")
    def exists(self): return os.path.exists(self.path)
    @contextmanager
    def locked(self):
        with open(self.lockpath, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(lf, fcntl.LOCK_UN)
    def load(self):
        with open(self.path) as f: return json.load(f)
    def _sha(self):
        with open(self.path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()
    def save(self, data):
        """Only the harness writes tasks.json. After every save we record its hash and a known-good copy,
        so a worker that edits it directly is detected and rolled back (see check_integrity)."""
        data["updated"] = now()
        fd, tmp = tempfile.mkstemp(dir=self.proj.state, prefix=".tasks.")
        with os.fdopen(fd, "w") as f: json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
        shutil.copyfile(self.path, self.proj.p(".tasks.good.json"))
        with open(self.proj.p("tasks.json.sha"), "w") as f: f.write(self._sha())
        render_plan(self.proj, data)
    def check_integrity(self):
        """True if tasks.json is exactly what the harness last wrote. Otherwise restore it and return False."""
        try:
            with open(self.proj.p("tasks.json.sha")) as f: want = f.read().strip()
        except OSError: return True  # legacy freeze without a sha: trust once, next save records it
        try: ok = self._sha() == want
        except OSError: ok = False
        if not ok and os.path.exists(self.proj.p(".tasks.good.json")):
            shutil.copyfile(self.proj.p(".tasks.good.json"), self.path)
        return ok

def get(data, tid):
    for t in data["tasks"]:
        if t["id"] == tid: return t
    return None

def next_task(data):
    done = {t["id"] for t in data["tasks"] if t["status"] == "done"}
    for t in data["tasks"]:
        if t["status"] in ("pending", "running") and all(d in done for d in t["deps"]):
            return t
    return None

def counts(data):
    c = {s: 0 for s in STATUSES}
    for t in data["tasks"]: c[t["status"]] += 1
    return c

_BOX = {"pending": " ", "running": " ", "done": "x", "blocked": "!"}

def render_plan(proj, data):
    """Regenerate PLAN.md (human view). Keeps `- [ ] ` / `- [x] ` lines the bash side counts."""
    out = [data.get("plan_head", "").rstrip(), "", "# Tasks", ""]
    for t in data["tasks"]:
        out.append(f"- [{_BOX[t['status']]}] {t['id']}. {t['title']}")
        if t.get("check"): out.append(f"  - check: `{t['check']}`")
        if t.get("files"): out.append(f"  - files: {', '.join(t['files'])}")
        if t["status"] == "blocked" and t.get("blocked_question"): out.append(f"  - blocked: {t['blocked_question']}")
    tmp = proj.p(".PLAN.md.tmp")
    with open(tmp, "w") as f: f.write("\n".join(out).strip() + "\n")
    os.replace(tmp, proj.p("PLAN.md"))
