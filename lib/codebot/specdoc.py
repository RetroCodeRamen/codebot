"""The specification, as the worker, the splitter and the check re-derivation see it. SPEC.md lives in the harness state directory, which the worker
may not read, so until 2026-10-17 the worker never saw it: its picture of the project was a one-line task title, the frozen checks and the code. Now
the harness packs the part a task needs into its context: the spec's introduction (everything before the first numbered requirement) and the text of
the requirement the task implements. The planner records which requirement each task implements (spec_items.json in the state directory); split
children and tasks the worker adds belong to their root task's requirement."""
import json, os, re
from . import taskclock as TC

MAP = "spec_items.json"

def load(proj):
    try:
        with open(proj.p("SPEC.md")) as f: return f.read()
    except OSError: return ""

def count_items(spec):
    """Number of numbered items ('1. ...' or '2) ...') at the start of a line."""
    return len(re.findall(r"^\s*\d+[.)]\s", spec, re.M))

def item_texts(spec):
    """The text of each numbered item, in order (an item runs to the next numbered line or the next heading)."""
    out, cur = [], None
    for ln in spec.splitlines():
        if re.match(r"^\s*\d+[.)]\s", ln):
            if cur is not None: out.append("\n".join(cur).strip())
            cur = [ln]
        elif cur is not None:
            if re.match(r"^\s*#", ln): out.append("\n".join(cur).strip()); cur = None
            else: cur.append(ln)
    if cur is not None: out.append("\n".join(cur).strip())
    return out

def intro(spec, cap=1500):
    """Everything before the first numbered item (title, stack, conventions), trimmed to cap characters."""
    m = re.search(r"^\s*\d+[.)]\s", spec, re.M)
    text = (spec[:m.start()] if m else spec).strip()
    return text if len(text) <= cap else text[:cap].rstrip() + " ..."

def save_map(proj, mapping):
    """{task id: requirement number} for the plan as written (rewritten whenever the plan is)."""
    tmp = proj.p(MAP + ".tmp")
    with open(tmp, "w") as f: json.dump(mapping, f, indent=1)
    os.replace(tmp, proj.p(MAP))

def load_map(proj):
    try:
        with open(proj.p(MAP)) as f: m = json.load(f)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError): return {}

def item_of(proj, task):
    """The requirement number a task (dict or id) implements, through its root task (T4.1 and tasks added while running T4 belong to T4), or None."""
    n = load_map(proj).get(TC.root_of(task))
    return n if isinstance(n, int) and n > 0 else None

def requirement(proj, task):
    """(number, text) of the task's requirement, or (None, "")."""
    n = item_of(proj, task); items = item_texts(load(proj))
    return (n, items[n - 1]) if n and n <= len(items) else (None, "")

def for_task(proj, task, cap=3500):
    """What the worker is shown: the introduction and the task's requirement; without a recorded requirement, the whole spec when it is short, else
    the introduction alone. "" when the project has no SPEC.md."""
    spec = load(proj)
    if not spec.strip(): return ""
    n, text = requirement(proj, task); total = count_items(spec)
    if n: out = f"{intro(spec)}\n\nThis task implements requirement {n} of {total}:\n{text}"
    elif len(spec) <= cap: out = spec.strip()
    else: out = intro(spec) + (f"\n\n(The specification has {total} numbered requirements; the task title says which part this task is.)" if total else "")
    return out if len(out) <= cap else out[:cap].rstrip() + " ..."
