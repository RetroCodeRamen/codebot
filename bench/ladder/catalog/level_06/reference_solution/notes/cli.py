import argparse, os, sys
from .storage import NoteStore, NoteNotFound, StorageError
from .validation import ValidationError
from .search import search as run_search
from . import transfer

class Fail(Exception):
    def __init__(self, msg, code): self.msg, self.code = msg, code

class Parser(argparse.ArgumentParser):
    def error(self, message): self.print_usage(sys.stderr); raise Fail(message, 2)

def fmt(n): return f"{n.id:>3} {'*' if n.pinned else ' '} {n.title}" + "".join(f" #{t}" for t in n.tags)

def order(notes): return sorted(notes, key=lambda n: (not n.pinned, n.id))

def build():
    ap = Parser(prog="notes"); sub = ap.add_subparsers(dest="cmd", parser_class=Parser)
    a = sub.add_parser("add"); a.add_argument("title"); a.add_argument("--body", default=""); a.add_argument("--tag", action="append", default=[]); a.add_argument("--pin", action="store_true")
    l = sub.add_parser("list"); l.add_argument("--tag", action="append", default=[]); l.add_argument("--pinned", action="store_true")
    sub.add_parser("show").add_argument("id")
    e = sub.add_parser("edit"); e.add_argument("id"); e.add_argument("--title"); e.add_argument("--body"); e.add_argument("--tag", action="append"); g = e.add_mutually_exclusive_group(); g.add_argument("--pin", action="store_true"); g.add_argument("--unpin", action="store_true")
    sub.add_parser("delete").add_argument("id")
    s = sub.add_parser("search"); s.add_argument("query", nargs="+"); s.add_argument("--tag", action="append", default=[])
    x = sub.add_parser("export"); x.add_argument("path"); x.add_argument("--format", default="json")
    i = sub.add_parser("import"); i.add_argument("path"); i.add_argument("--on-conflict", default="skip")
    return ap

def nid(raw):
    try: return int(raw)
    except ValueError: raise Fail(f"invalid note id '{raw}'", 2)

def main(argv, store=None, out=None):
    out = out or sys.stdout
    def say(s=""): print(s, file=out)
    try:
        ns = build().parse_args(argv)
        if not ns.cmd: build().print_usage(sys.stderr); raise Fail("a command is required", 2)
        if store is None:
            now = os.environ.get("NOTES_NOW"); store = NoteStore(os.environ.get("NOTES_FILE") or "notes.json", clock=(lambda: now) if now else None)
        c = ns.cmd
        if c == "add": n = store.add(ns.title, ns.body, ns.tag, ns.pin); say(f"Added note {n.id}")
        elif c == "list":
            notes = order(run_search(store.all(), "", ns.tag)); notes = [n for n in notes if n.pinned] if ns.pinned else notes
            say("\n".join(fmt(n) for n in notes) if notes else "No notes.")
        elif c == "show":
            n = store.get(nid(ns.id)); say(f"Note {n.id}: {n.title}"); say(f"Created: {n.created}"); say(f"Updated: {n.updated}")
            if n.tags: say(f"Tags: {', '.join(n.tags)}")
            if n.pinned: say("Pinned")
            say(); say(n.body)
        elif c == "edit":
            f = {}
            if ns.title is not None: f["title"] = ns.title
            if ns.body is not None: f["body"] = ns.body
            if ns.tag is not None: f["tags"] = ns.tag
            if ns.pin: f["pinned"] = True
            if ns.unpin: f["pinned"] = False
            if not f: raise Fail("nothing to edit", 2)
            n = store.update(nid(ns.id), **f); say(f"Updated note {n.id}")
        elif c == "delete": n = store.delete(nid(ns.id)); say(f"Deleted note {n.id}")
        elif c == "search":
            hits = run_search(store.all(), " ".join(ns.query), ns.tag); say("\n".join(fmt(n) for n in hits) if hits else "No matches.")
        elif c == "export":
            if ns.format not in ("json", "markdown"): raise Fail(f"invalid format '{ns.format}'", 2)
            k = (transfer.export_json if ns.format == "json" else transfer.export_markdown)(store, ns.path); say(f"Exported {k} note(s) to {ns.path}")
        elif c == "import":
            if ns.on_conflict not in ("skip", "overwrite", "duplicate"): raise Fail(f"invalid conflict mode '{ns.on_conflict}'", 2)
            try: r = transfer.import_json(store, ns.path, ns.on_conflict)
            except FileNotFoundError: raise Fail(f"no such file: {ns.path}", 2)
            say(f"Imported: added {r['added']}, skipped {r['skipped']}, overwritten {r['overwritten']}")
        return 0
    except Fail as f:
        if f.msg: print(f"Error: {f.msg}", file=sys.stderr)
        return f.code
    except NoteNotFound as e: print(f"Error: no note with id {e.args[0]}", file=sys.stderr); return 1
    except (ValidationError, transfer.TransferError, StorageError) as e: print(f"Error: {e}", file=sys.stderr); return 2
    except SystemExit as s: return s.code if isinstance(s.code, int) else 2
