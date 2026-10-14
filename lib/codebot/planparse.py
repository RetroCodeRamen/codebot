"""Parse the planner's PLAN.md (tasks with optional `check:` / `files:` / `size:` sub-lines) into task dicts."""
import re
from .tasks import new_task

TASK_RE = re.compile(r"^\s*[-*]\s*\[([ xX!])\]\s*(?:T)?(\d+(?:\.\d+)*)[.)]?\s+(.*\S)\s*$")
SUB_RE = re.compile(r"^\s+[-*]\s*(check|files|size|deps)\s*:\s*(.*\S)\s*$", re.I)

def _strip_ticks(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == "`": s = s[1:-1]
    return s.strip()

def parse_plan(text):
    """returns (head, tasks). head = everything before the '# Tasks' heading."""
    m = re.search(r"^#\s*Tasks\s*$", text, re.M)
    head, body = (text[:m.start()], text[m.end():]) if m else (text, "")
    tasks, cur = [], None
    for ln in body.splitlines():
        tm = TASK_RE.match(ln)
        if tm:
            box, num, title = tm.groups()
            cur = new_task("T" + num, title)
            if box in "xX": cur["status"] = "done"
            if box == "!": cur["status"] = "blocked"
            tasks.append(cur); continue
        sm = SUB_RE.match(ln)
        if sm and cur is not None:
            k, v = sm.group(1).lower(), sm.group(2)
            if k == "check": cur["check"] = _strip_ticks(v)
            elif k == "files": cur["files"] = [f.strip(" `") for f in v.split(",") if f.strip(" `")]
            elif k == "size": cur["size"] = v.strip().upper()[:1] or "M"
            elif k == "deps": cur["deps"] = [("T" + d.strip().lstrip("T")) for d in v.split(",") if d.strip()]
    return head.rstrip() + "\n", tasks
