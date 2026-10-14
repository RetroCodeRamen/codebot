"""tasks.json: the structured task list. PLAN.md is rendered from it; the model never edits either."""
import json, os, fcntl, tempfile, re
from contextlib import contextmanager
from .config import now

STATUSES = ("pending", "running", "done", "blocked")

def new_task(tid, title, check=None, files=None, size=None, deps=None, parent=None):
    return {"id": tid, "title": title, "check": check, "files": files or [], "size": size or "M",
            "status": "pending", "deps": deps or [], "parent": parent, "attempts": 0, "ladder": 0,
            "blocked_question": None, "history": []}

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
    def save(self, data):
        data["updated"] = now()
        fd, tmp = tempfile.mkstemp(dir=self.proj.state, prefix=".tasks.")
        with os.fdopen(fd, "w") as f: json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
        render_plan(self.proj, data)

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
