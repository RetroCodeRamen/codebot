"""Parse the planner's PLAN.md (tasks with optional `check:` / `files:` / `size:` sub-lines) into task dicts."""
import re
from .tasks import new_task

TASK_RE = re.compile(r"^\s*[-*]\s*\[([ xX!])\]\s*(?:T)?(\d+(?:\.\d+)*)[.)]?\s+(.*\S)\s*$")
SUB_RE = re.compile(r"^\s+[-*]\s*(check|files|size|deps)\s*:\s*(.*\S)\s*$", re.I)

def _split_check(s):
    """(command, trailing_prose_or_None). A check is ONE command in backticks; prose after the closing backtick is a planner mistake
    (the shell would try to run it), so it is reported separately instead of becoming part of the command."""
    s = s.strip()
    m = re.match(r"^`([^`]+)`\s*(.*)$", s)
    if m: return m.group(1).strip(), (m.group(2).strip() or None)
    return s.strip("`").strip(), None

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
            if k == "check":
                cur["check"], note = _split_check(v)
                if note: cur["check_note"] = note
                else: cur.pop("check_note", None)
            elif k == "files": cur["files"] = [f.strip(" `") for f in v.split(",") if f.strip(" `")]
            elif k == "size": cur["size"] = v.strip().upper()[:1] or "M"
            elif k == "deps": cur["deps"] = [("T" + d.strip().lstrip("T")) for d in v.split(",") if d.strip()]
    return head.rstrip() + "\n", tasks
