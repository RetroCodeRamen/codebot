"""The command line."""
import argparse, os, sys, time
from .errors import AuthError, NotFound, PermissionDenied, StoreError, ValidationError
from .http_api import make_server
from .store import NoteStore
from .sync import sync_remote
from .worker import Worker

def line(n): return f"{n['id']:>3}  {n['title']}" + (("  " + " ".join("#" + t for t in n["tags"])) if n.get("tags") else "")

def main(argv=None, out=None):
    out = out or sys.stdout; say = lambda s="": print(s, file=out)
    ap = argparse.ArgumentParser(prog="notes"); sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("add"); a.add_argument("title"); a.add_argument("--body", default=""); a.add_argument("--tag", action="append", default=[])
    l = sub.add_parser("list"); l.add_argument("--tag", action="append", default=[]); sub.add_parser("show").add_argument("id", type=int); sub.add_parser("delete").add_argument("id", type=int)
    e = sub.add_parser("edit"); e.add_argument("id", type=int); e.add_argument("--title"); e.add_argument("--body"); e.add_argument("--tag", action="append")
    q = sub.add_parser("search"); q.add_argument("words", nargs="+"); q.add_argument("--tag", action="append", default=[]); sub.add_parser("tags"); sub.add_parser("stats"); u = sub.add_parser("adduser"); u.add_argument("name"); u.add_argument("password"); rm = sub.add_parser("remind"); rm.add_argument("id", type=int); rm.add_argument("due", nargs="?"); rm.add_argument("--clear", action="store_true"); wk = sub.add_parser("worker"); wk.add_argument("--once", action="store_true"); wk.add_argument("--interval", type=float, default=1.0); nf = sub.add_parser("notifications"); nf.add_argument("--user"); nf.add_argument("--unread", action="store_true"); dv = sub.add_parser("device"); sy = sub.add_parser("sync"); sy.add_argument("target"); sh = sub.add_parser("share"); sh.add_argument("id", type=int); sh.add_argument("user"); sh.add_argument("mode"); sv = sub.add_parser("serve"); sv.add_argument("--auth", action="store_true"); sv.add_argument("--host", default="127.0.0.1"); sv.add_argument("--port", type=int, default=8080)
    try:
        ns = ap.parse_args(argv)
        if not ns.cmd: ap.print_usage(sys.stderr); return 2
        s = NoteStore(os.environ.get("NOTES_FILE") or "notes.json")
        if ns.cmd == "add": say(f"Added note {s.add(ns.title, ns.body, ns.tag)['id']}")
        elif ns.cmd == "list": ns_ = s.search("", ns.tag); say("\n".join(line(n) for n in ns_) if ns_ else "No notes.")
        elif ns.cmd == "search": ns_ = s.search(" ".join(ns.words), ns.tag); say("\n".join(line(n) for n in ns_) if ns_ else "No matches.")
        elif ns.cmd == "serve":
            srv = make_server(s, ns.host, ns.port, ns.auth); say(f"Serving on http://{ns.host}:{srv.port}"); out.flush()
            try: srv.serve_forever()
            except KeyboardInterrupt: pass
            finally: srv.server_close()
        elif ns.cmd == "remind":
            if ns.clear: s.clear_reminder(ns.id); say(f"Reminder cleared for note {ns.id}")
            elif ns.due is None: print("Error: give a time or --clear", file=sys.stderr); return 2
            else: s.set_reminder(ns.id, ns.due); say(f"Reminder set for note {ns.id} at {ns.due}")
        elif ns.cmd == "worker":
            w = Worker(s, ns.interval)
            if ns.once: r = w.run_once(); say(f"Fired {len(r['fired'])} reminder(s)")
            else:
                say("Worker running"); out.flush()
                try:
                    while True: r = w.run_once(); [say(f"Fired reminder of note {i}") for i in r["fired"]]; out.flush(); time.sleep(ns.interval)
                except KeyboardInterrupt: pass
        elif ns.cmd == "notifications":
            items = s.notifications(ns.user, ns.unread); say("\n".join(f"[{n['id']}] {n['ts']} {n['message']}" + ("" if n["read"] else " (new)") for n in items) if items else "No notifications.")
        elif ns.cmd == "device": say(s.device_id)
        elif ns.cmd == "sync":
            r = sync_remote(s, ns.target) if ns.target.startswith(("http://", "https://")) else s.sync_with(NoteStore(ns.target))
            say(f"Synced with {ns.target}: pulled {r['pulled']}, pushed {r['pushed']}, conflicts {r['conflicts']}")
        elif ns.cmd == "adduser": say(f"Added user {s.create_user(ns.name, ns.password)['name']}")
        elif ns.cmd == "share": s.share(ns.id, ns.user, ns.mode); say(f"Shared note {ns.id} with {ns.user} ({ns.mode})")
        elif ns.cmd == "stats": st = s.stats(); say(f"Notes: {st['notes']}"); say(f"Tags: {st['tags']}")
        elif ns.cmd == "tags": ts = s.tags(); say("\n".join(f"{t}: {c}" for t, c in ts) if ts else "No tags.")
        elif ns.cmd == "show": n = s.get(ns.id); say(n["title"]); say(n["body"])
        elif ns.cmd == "delete": say(f"Deleted note {s.delete(ns.id)['id']}")
        elif ns.cmd == "edit":
            if ns.title is None and ns.body is None and ns.tag is None: print("Error: nothing to edit", file=sys.stderr); return 2
            say(f"Updated note {s.update(ns.id, ns.title, ns.body, ns.tag)['id']}")
        return 0
    except NotFound as e: print(f"Error: no note with id {e.args[0]}" if isinstance(e.args[0], int) else f"Error: not found: {e.args[0]}", file=sys.stderr); return 1
    except PermissionDenied as e: print(f"Error: {e}", file=sys.stderr); return 3
    except AuthError as e: print(f"Error: {e}", file=sys.stderr); return 2
    except (ValidationError, StoreError) as e: print(f"Error: {e}", file=sys.stderr); return 2
    except SystemExit as e: return e.code if isinstance(e.code, int) else 2

