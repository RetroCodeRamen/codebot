import argparse, datetime, json, os, re, sys, tempfile

PRIOS = ("high", "medium", "low")

class Fail(Exception):
    def __init__(self, msg, code=2): self.msg, self.code = msg, code

class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr); raise Fail(message, 2)

def path(): return os.environ.get("TODO_FILE") or "tasks.json"
def today(): return os.environ.get("TODO_TODAY") or datetime.date.today().isoformat()

def load():
    p = path()
    if not os.path.exists(p): return {"next_id": 1, "tasks": []}
    try:
        with open(p) as f: d = json.load(f)
        assert isinstance(d, dict) and isinstance(d.get("next_id"), int) and isinstance(d.get("tasks"), list)
        for t in d["tasks"]:
            assert isinstance(t, dict) and isinstance(t["id"], int) and isinstance(t["title"], str) and isinstance(t["done"], bool)
            assert t["priority"] in PRIOS and (t["due"] is None or isinstance(t["due"], str)) and isinstance(t["tags"], list)
        return d
    except (ValueError, AssertionError, KeyError, TypeError, OSError): raise Fail("data file is corrupt", 2)

def save(d):
    p = path(); fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(p)), prefix=".todo-")
    with os.fdopen(fd, "w") as f: json.dump(d, f, indent=1)
    os.replace(tmp, p)

def prio(v):
    if v not in PRIOS: raise Fail(f"invalid priority '{v}'")
    return v

def date(v):
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v): raise ValueError(v)
        return datetime.date.fromisoformat(v).isoformat()
    except ValueError: raise Fail(f"invalid due date '{v}'")

def tag_list(existing, add):
    out = list(existing)
    for t in add:
        t = t.strip().lower()
        if t and t not in out: out.append(t)
    return out

def ids(d, raw):
    out = []
    for r in raw:
        try: out.append(int(r))
        except ValueError: raise Fail(f"invalid task id '{r}'")
    missing = [i for i in out if not any(t["id"] == i for t in d["tasks"])]
    if missing:
        for i in missing: print(f"Error: no task with id {i}", file=sys.stderr)
        raise Fail(None, 1)
    return out

def find(d, i): return next(t for t in d["tasks"] if t["id"] == i)

def line(t):
    s = f"{t['id']:>3} [{'x' if t['done'] else ' '}] {t['priority']:<6} {t['title']}"
    if t["due"]: s += f" (due {t['due']})"
    return s + "".join(f" #{g}" for g in t["tags"])

def run(argv):
    ap = Parser(prog="todo.py"); sub = ap.add_subparsers(dest="cmd", parser_class=Parser)
    a = sub.add_parser("add"); a.add_argument("title"); a.add_argument("--priority", default="medium"); a.add_argument("--due"); a.add_argument("--tag", action="append", default=[])
    l = sub.add_parser("list"); l.add_argument("--all", action="store_true"); l.add_argument("--done", action="store_true"); l.add_argument("--tag"); l.add_argument("--priority"); l.add_argument("--sort", default="id")
    for n in ("done", "reopen", "delete"): sub.add_parser(n).add_argument("ids", nargs="+")
    sub.add_parser("clear-done"); sub.add_parser("stats")
    e = sub.add_parser("edit"); e.add_argument("id"); e.add_argument("--title"); e.add_argument("--priority"); e.add_argument("--due"); e.add_argument("--add-tag", action="append", default=[]); e.add_argument("--remove-tag", action="append", default=[])
    ns = ap.parse_args(argv)
    if not ns.cmd: ap.print_usage(sys.stderr); raise Fail("a command is required", 2)
    d = load(); c = ns.cmd
    if c == "add":
        title = ns.title.strip()
        if not title: raise Fail("title must not be empty")
        pr = prio(ns.priority); due = date(ns.due) if ns.due is not None else None
        t = {"id": d["next_id"], "title": title, "done": False, "priority": pr, "due": due, "tags": tag_list([], ns.tag)}
        d["tasks"].append(t); d["next_id"] += 1; save(d); print(f"Added task {t['id']}: {title}")
    elif c == "list":
        if ns.all and ns.done: raise Fail("--all and --done cannot be combined")
        if ns.sort not in ("id", "priority", "due"): raise Fail(f"invalid sort key '{ns.sort}'")
        if ns.priority is not None: prio(ns.priority)
        ts = [t for t in d["tasks"] if ns.all or (t["done"] if ns.done else not t["done"])]
        if ns.tag: ts = [t for t in ts if ns.tag.lower() in t["tags"]]
        if ns.priority: ts = [t for t in ts if t["priority"] == ns.priority]
        key = {"id": lambda t: t["id"], "priority": lambda t: (PRIOS.index(t["priority"]), t["id"]), "due": lambda t: (t["due"] is None, t["due"] or "", t["id"])}[ns.sort]
        ts.sort(key=key); print("\n".join(line(t) for t in ts) if ts else "No tasks.")
    elif c in ("done", "reopen"):
        for i in ids(d, ns.ids):
            t = find(d, i)
            if c == "done":
                print(f"Task {i} is already done" if t["done"] else f"Completed task {i}"); t["done"] = True
            else: t["done"] = False; print(f"Reopened task {i}")
        save(d)
    elif c == "delete":
        want = ids(d, ns.ids); d["tasks"] = [t for t in d["tasks"] if t["id"] not in want]; save(d)
        for i in want: print(f"Deleted task {i}")
    elif c == "clear-done":
        n = sum(1 for t in d["tasks"] if t["done"]); d["tasks"] = [t for t in d["tasks"] if not t["done"]]
        if n: save(d)
        print(f"Removed {n} completed task(s)")
    elif c == "edit":
        i = ids(d, [ns.id])[0]; t = find(d, i)
        if ns.title is None and ns.priority is None and ns.due is None and not ns.add_tag and not ns.remove_tag: raise Fail("nothing to edit")
        title = ns.title.strip() if ns.title is not None else t["title"]
        if not title: raise Fail("title must not be empty")
        pr = prio(ns.priority) if ns.priority is not None else t["priority"]
        due = t["due"] if ns.due is None else (None if ns.due == "none" else date(ns.due))
        tags = [g for g in tag_list(t["tags"], ns.add_tag) if g not in [r.strip().lower() for r in ns.remove_tag]]
        t.update(title=title, priority=pr, due=due, tags=tags); save(d); print(f"Updated task {i}")
    elif c == "stats":
        ts = d["tasks"]; print(f"Total: {len(ts)}\nDone: {sum(t['done'] for t in ts)}\nPending: {sum(not t['done'] for t in ts)}\nOverdue: {sum(1 for t in ts if not t['done'] and t['due'] and t['due'] < today())}")

def main(argv=None):
    try: run(sys.argv[1:] if argv is None else argv)
    except Fail as f:
        if f.msg: print(f"Error: {f.msg}", file=sys.stderr)
        return f.code
    except SystemExit as s: return s.code if isinstance(s.code, int) else 2
    return 0

if __name__ == "__main__": sys.exit(main())
