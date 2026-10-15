"""Command line: python3 -m helpdesk [--file PATH] [--as USER_ID] <command> ..."""
import argparse, os, sys
from . import config, digest, export, housekeeping, permissions, report_formats, sla_report, timeline, user_csv, reports, search as searchmod, stats, templates
from .app import Helpdesk
from .errors import HelpdeskError, NotFound, PermissionDenied, ValidationError

class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr); raise ValidationError(message)

def build_parser():
    ap = Parser(prog="helpdesk"); ap.add_argument("--file"); ap.add_argument("--as", dest="actor", type=int)
    sub = ap.add_subparsers(dest="cmd", parser_class=Parser)
    u = sub.add_parser("user").add_subparsers(dest="sub", parser_class=Parser)
    a = u.add_parser("add"); a.add_argument("name"); a.add_argument("--role", default="customer"); a.add_argument("--email", default="")
    u.add_parser("list"); u.add_parser("disable").add_argument("id", type=int)
    r = u.add_parser("role"); r.add_argument("id", type=int); r.add_argument("role")
    t = sub.add_parser("ticket").add_subparsers(dest="sub", parser_class=Parser)
    a = t.add_parser("add"); a.add_argument("title"); a.add_argument("--description", required=True); a.add_argument("--priority", default="normal"); a.add_argument("--severity"); a.add_argument("--category", default="general"); a.add_argument("--requester", type=int)
    l = t.add_parser("list"); l.add_argument("--status"); l.add_argument("--assignee", type=int); l.add_argument("--priority"); l.add_argument("--severity"); l.add_argument("--label"); l.add_argument("--category")
    a = t.add_parser("severity"); a.add_argument("id", type=int); a.add_argument("severity")
    lb = t.add_parser("label").add_subparsers(dest="label_sub", parser_class=Parser)
    for name in ("add", "remove"):
        a = lb.add_parser(name); a.add_argument("id", type=int); a.add_argument("label")
    a = t.add_parser("merge"); a.add_argument("source", type=int); a.add_argument("target", type=int)
    t.add_parser("show").add_argument("id", type=int)
    a = t.add_parser("assign"); a.add_argument("id", type=int); a.add_argument("user", type=int)
    a = t.add_parser("status"); a.add_argument("id", type=int); a.add_argument("status"); a.add_argument("--resolution")
    a = t.add_parser("priority"); a.add_argument("id", type=int); a.add_argument("priority")
    a = t.add_parser("comment"); a.add_argument("id", type=int); a.add_argument("body"); a.add_argument("--internal", action="store_true")
    a = t.add_parser("watch"); a.add_argument("id", type=int); a.add_argument("--user", type=int); a.add_argument("--remove", action="store_true")
    a = t.add_parser("attach"); a.add_argument("id", type=int); a.add_argument("name"); a.add_argument("--size", type=int, required=True); a.add_argument("--type", default="application/octet-stream")
    t.add_parser("timeline").add_argument("id", type=int)
    t.add_parser("autoassign").add_argument("id", type=int)
    a = t.add_parser("link"); a.add_argument("id", type=int); a.add_argument("kind"); a.add_argument("target", type=int); a.add_argument("--remove", action="store_true")
    s = sub.add_parser("search"); s.add_argument("query", nargs="+")
    sv = sub.add_parser("saved").add_subparsers(dest="sub", parser_class=Parser)
    a = sv.add_parser("add"); a.add_argument("name"); a.add_argument("query", nargs="+"); sv.add_parser("list"); sv.add_parser("run").add_argument("name"); sv.add_parser("delete").add_argument("name")
    rp = sub.add_parser("report"); rp.add_argument("kind", choices=["summary", "sla", "workload", "compliance", "audit"]); rp.add_argument("--format", choices=report_formats.FORMATS, default="text")
    n = sub.add_parser("notifications"); n.add_argument("--all", action="store_true"); n.add_argument("--read", action="store_true")
    e = sub.add_parser("export"); e.add_argument("format", choices=["csv", "json"]); e.add_argument("path")
    sub.add_parser("escalate").add_argument("--dry-run", action="store_true")
    b = sub.add_parser("bulk").add_subparsers(dest="sub", parser_class=Parser)
    b.add_parser("close-stale").add_argument("days", type=int)
    a = b.add_parser("reassign"); a.add_argument("from_user", type=int); a.add_argument("to_user", type=int)
    tp = sub.add_parser("template").add_subparsers(dest="sub", parser_class=Parser)
    tp.add_parser("list"); a = tp.add_parser("reply"); a.add_argument("ticket", type=int); a.add_argument("name"); a.add_argument("--internal", action="store_true")
    sub.add_parser("import").add_argument("path")
    sub.add_parser("stats").add_argument("kind", choices=["agents", "response"])
    sub.add_parser("digest")
    rl = sub.add_parser("rules").add_subparsers(dest="sub", parser_class=Parser)
    rl.add_parser("list"); rl.add_parser("apply").add_argument("id", type=int)
    ck = sub.add_parser("check"); ck.add_argument("--repair", action="store_true")
    sub.add_parser("permissions")
    au = sub.add_parser("audit").add_subparsers(dest="sub", parser_class=Parser)
    a = au.add_parser("list"); a.add_argument("--entity"); a.add_argument("--id", type=int); a.add_argument("--actor", type=int, dest="actor_filter"); a.add_argument("--action"); a.add_argument("--outcome"); a.add_argument("--limit", type=int)
    pg = sub.add_parser("purge"); pg.add_argument("days", type=int)
    sub.add_parser("kb").add_argument("ticket", type=int)
    return ap

def line(t):
    return f"#{t.id:<4} {t.status:<11} {t.priority:<7} {t.title}"

def show(hd, ticket, user):
    s = hd.store; who = lambda uid: f"{s.users[uid].name} (#{uid})" if uid in s.users else "-"
    out = [f"Ticket #{ticket.id}: {ticket.title}", f"Status: {ticket.status}", f"Priority: {ticket.priority}", f"Category: {ticket.category}", f"Requester: {who(ticket.requester_id)}",
           f"Assignee: {who(ticket.assignee_id)}", f"Created: {ticket.created}", f"Updated: {ticket.updated}", f"SLA due: {ticket.sla_due or '-'}"]
    if ticket.resolution: out.append(f"Resolution: {ticket.resolution}")
    out.append(f"Severity: {ticket.severity}")
    if ticket.labels: out.append(f"Labels: {', '.join(ticket.labels)}")
    if ticket.merged_into is not None: out.append(f"Merged into: #{ticket.merged_into}")
    for l in hd.links.describe(ticket.id): out.append(f"Link: {l}")
    out += ["", ticket.description]
    comments = hd.tickets.visible_comments(ticket, user)
    if comments:
        out += ["", f"Comments ({len(comments)}):"]
        for c in comments: out.append(f"  [{c.ts}] {s.users[c.author_id].name}: {'[internal] ' if c.internal else ''}{c.body}")
    return "\n".join(out)

def run(argv, out):
    ns = build_parser().parse_args(argv)
    if not ns.cmd: raise ValidationError("a command is required")
    hd = Helpdesk(ns.file); say = lambda s="": print(s, file=out)
    actor_id = ns.actor if ns.actor is not None else (int(os.environ[config.USER_ENV]) if os.environ.get(config.USER_ENV, "").isdigit() else None)
    def actor():
        if actor_id is None: raise ValidationError("this command needs an acting user: use --as USER_ID")
        return hd.users.get(actor_id)
    c = ns.cmd
    if c == "user":
        if ns.sub == "add":
            u = hd.users.create_user(ns.name, ns.role, ns.email, actor_id=actor().id if hd.store.users else None); say(f"Added user {u.id}: {u.name} ({u.role})")
        elif ns.sub == "list":
            for u in hd.users.list(): say(f"{u.id:>3} {u.name} ({u.role}){'' if u.active else ' [disabled]'}")
        elif ns.sub == "disable": u = hd.users.disable(ns.id, actor().id); say(f"Disabled user {u.id}")
        elif ns.sub == "role": u = hd.users.set_role(ns.id, ns.role, actor().id); say(f"User {u.id} is now {u.role}")
        else: raise ValidationError("a user subcommand is required")
    elif c == "ticket":
        me = actor(); sv = hd.tickets
        if ns.sub == "add": t = sv.create(ns.requester if ns.requester is not None else me.id, ns.title, ns.description, ns.priority, ns.category, actor_id=me.id, severity=ns.severity); say(f"Created ticket #{t.id}: {t.title}")
        elif ns.sub == "list":
            ts = sv.list(me.id, ns.status, ns.assignee, ns.priority, ns.category, severity=ns.severity, label=ns.label); say("\n".join(line(t) for t in ts) if ts else "No tickets.")
        elif ns.sub == "show": say(show(hd, sv.get(ns.id, me.id), me))
        elif ns.sub == "assign": t = sv.assign(ns.id, ns.user, me.id); say(f"Assigned ticket #{t.id} to {hd.store.users[t.assignee_id].name}")
        elif ns.sub == "status": t = sv.set_status(ns.id, ns.status, me.id, ns.resolution); say(f"Ticket #{t.id} is now {t.status}")
        elif ns.sub == "priority": t = sv.set_priority(ns.id, ns.priority, me.id); say(f"Ticket #{t.id} priority set to {t.priority}")
        elif ns.sub == "severity": t = sv.set_severity(ns.id, ns.severity, me.id); say(f"Ticket #{t.id} severity set to {t.severity}")
        elif ns.sub == "label":
            if ns.label_sub == "add": t = sv.add_label(ns.id, ns.label, me.id); say(f"Ticket #{t.id} labels: {', '.join(t.labels)}")
            elif ns.label_sub == "remove": t = sv.remove_label(ns.id, ns.label, me.id); say(f"Ticket #{t.id} labels: {', '.join(t.labels) or '-'}")
            else: raise ValidationError("a label subcommand is required")
        elif ns.sub == "merge": n = sv.merge(ns.source, ns.target, me.id); say(f"Merged ticket #{ns.source} into #{ns.target} ({n} comment(s) moved)")
        elif ns.sub == "comment": cm = sv.add_comment(ns.id, me.id, ns.body, ns.internal); say(f"Added comment {cm.id} to ticket #{cm.ticket_id}")
        elif ns.sub == "watch":
            uid = ns.user if ns.user is not None else me.id
            if ns.remove: sv.unwatch(ns.id, uid, me.id); say(f"User {uid} no longer watches ticket #{ns.id}")
            else: sv.watch(ns.id, uid, me.id); say(f"User {uid} now watches ticket #{ns.id}")
        elif ns.sub == "timeline": say(timeline.render(sv.get(ns.id, me.id), hd.store, show_internal=permissions.can(me, "internal_comment")))
        elif ns.sub == "autoassign": t = hd.balancer.autoassign(ns.id, me.id); say(f"Assigned ticket #{t.id} to {hd.store.users[t.assignee_id].name}")
        elif ns.sub == "link":
            if ns.remove: hd.links.unlink(ns.id, ns.kind, ns.target, me.id); say(f"Removed {ns.kind} link from #{ns.id} to #{ns.target}")
            else: hd.links.link(ns.id, ns.kind, ns.target, me.id); say(f"Linked #{ns.id} {ns.kind} #{ns.target}")
        elif ns.sub == "attach": a = sv.add_attachment(ns.id, ns.name, ns.size, ns.type, me.id); say(f"Attached {a['name']} ({a['size']} bytes) to ticket #{ns.id}")
        else: raise ValidationError("a ticket subcommand is required")
    elif c == "search":
        me = actor(); visible = hd.tickets.list(me.id); ts = searchmod.search(visible, " ".join(ns.query)); say("\n".join(line(t) for t in ts) if ts else "No matches.")
    elif c == "saved":
        me = actor()
        if ns.sub == "add": r = hd.saved.save(me.id, ns.name, " ".join(ns.query)); say(f"Saved search '{r['name']}'")
        elif ns.sub == "list": items = hd.saved.list(me.id); say("\n".join(f"{x['name']}: {x['query']}" for x in items) if items else "No saved searches.")
        elif ns.sub == "run": ts = hd.saved.run(me.id, ns.name); say("\n".join(line(t) for t in ts) if ts else "No matches.")
        elif ns.sub == "delete": hd.saved.delete(me.id, ns.name); say(f"Deleted saved search '{ns.name}'")
        else: raise ValidationError("a saved subcommand is required")
    elif c == "report":
        permissions.require(actor(), "report"); now = hd.clock()
        if ns.kind == "summary": say(report_formats.render_summary(reports.summary(hd.store, now), ns.format))
        elif ns.kind == "audit":
            counts = hd.audit.count_by_action(); say("\n".join(f"{a}: {n}" for a, n in counts.items()) if counts else "The audit log is empty.")
        elif ns.kind == "compliance":
            for p, row in sla_report.compliance(hd.store, now).items(): say(f"{p}: total {row['total']}, on time {row['on_time']}, late {row['late']}, overdue {row['overdue']}, running {row['running']}")
        elif ns.kind == "sla":
            late = reports.sla_breaches(hd.store, now); say("\n".join(f"#{t.id} due {t.sla_due} {t.title}" for t in late) if late else "No SLA breaches.")
        else:
            w = reports.workload(hd.store); say("\n".join(f"{hd.store.users[uid].name}: {n}" for uid, n in w.items()) if w else "No assigned tickets.")
    elif c == "notifications":
        me = actor(); items = hd.notifier.all_for(me.id) if ns.all else hd.notifier.unread(me.id)
        say("\n".join(f"[{n['id']}] {n['ts']} {n['message']}" + (" (read)" if n["read"] else "") for n in items) if items else "No notifications.")
        if ns.read: hd.notifier.mark_read(me.id)
    elif c == "export":
        permissions.require(actor(), "export"); k = (export.export_csv if ns.format == "csv" else export.export_json)(hd.store, ns.path); say(f"Exported {k} ticket(s) to {ns.path}")
    else: run_extra(ns, hd, actor, say)

def run_extra(ns, hd, actor, say):
    c = ns.cmd
    if c == "escalate":
        me = actor(); permissions.require(me, "change_priority")
        done = hd.escalator.preview() if ns.dry_run else hd.escalator.run(me.id)
        say("\n".join(f"#{tid} {old} -> {new}" for tid, old, new in done) if done else "Nothing to escalate.")
    elif c == "bulk":
        me = actor()
        if ns.sub == "close-stale": ids = hd.bulk.close_stale(ns.days, me.id); say(f"Closed {len(ids)} ticket(s)" + (": " + ", ".join(f"#{i}" for i in ids) if ids else ""))
        elif ns.sub == "reassign": ids = hd.bulk.reassign(ns.from_user, ns.to_user, me.id); say(f"Reassigned {len(ids)} ticket(s)" + (": " + ", ".join(f"#{i}" for i in ids) if ids else ""))
        else: raise ValidationError("a bulk subcommand is required")
    elif c == "template":
        if ns.sub == "list": say("\n".join(templates.names()))
        elif ns.sub == "reply": cm = hd.replier.reply(ns.ticket, ns.name, actor().id, ns.internal); say(f"Added comment {cm.id} to ticket #{cm.ticket_id}")
        else: raise ValidationError("a template subcommand is required")
    elif c == "import":
        me = actor(); permissions.require(me, "view_all")
        try: created, errors = hd.importer.run(ns.path, me.id)
        except (OSError, ValueError) as e: raise ValidationError(f"cannot import {ns.path}: {e}")
        say(f"Imported {len(created)} ticket(s)")
        for e in errors: say(e)
    elif c == "stats":
        permissions.require(actor(), "report")
        if ns.kind == "agents":
            rows = stats.agent_performance(hd.store); say("\n".join(f"{hd.store.users[u].name}: assigned {r['assigned']}, closed {r['closed']}, avg close hours {r['avg_close_hours']}" for u, r in rows.items()) if rows else "No assigned tickets.")
        else:
            r = stats.summary_percentiles(hd.store); say(f"First responses: {r['count']}"); say(f"Median hours: {r['p50']}"); say(f"90th percentile hours: {r['p90']}")
    elif c == "rules":
        me = actor()
        if ns.sub == "list": rules = hd.rules.rules(); say("\n".join(f"{i}. {r.get('name', 'unnamed rule')}" for i, r in enumerate(rules, 1)) if rules else "No rules.")
        elif ns.sub == "apply": done = hd.rules.apply(ns.id, me.id); say("\n".join(done) if done else "No rule applied.")
        else: raise ValidationError("a rules subcommand is required")
    elif c == "check":
        permissions.require(actor(), "manage_users"); problems = housekeeping.check_integrity(hd.store)
        say("\n".join(problems) if problems else "No problems found.")
        if ns.repair and problems: say(f"Repaired {housekeeping.repair(hd.store)} item(s)")
    elif c == "permissions": say(permissions.matrix())
    elif c == "audit":
        permissions.require(actor(), "audit")
        if ns.sub != "list": raise ValidationError("an audit subcommand is required")
        es = hd.audit.entries(ns.entity, ns.id, ns.actor_filter, ns.action, ns.outcome)
        if ns.limit: es = es[-ns.limit:]
        say("\n".join(f"#{e['seq']} {e['ts']} actor={e['actor'] if e['actor'] is not None else '-'} {e['action']} {e['entity']}:{e['entity_id'] if e['entity_id'] is not None else '-'} {e['outcome']}" for e in es) if es else "No audit entries.")
    elif c == "digest": say(digest.build_digest(hd.store, hd.notifier, actor().id))
    elif c == "purge": ids = hd.retention.purge(ns.days, actor().id); say(f"Purged {len(ids)} ticket(s)" + (": " + ", ".join(f"#{i}" for i in ids) if ids else ""))
    elif c == "kb":
        me = actor(); t = hd.tickets.get(ns.ticket, me.id); arts = hd.kb.suggest(t)
        say("\n".join(f"- {a['title']}" for a in arts) if arts else "No suggestions.")

def main(argv=None, out=None):
    out = out or sys.stdout
    try: run(sys.argv[1:] if argv is None else argv, out); return 0
    except NotFound as e: print(f"Error: {e}", file=sys.stderr); return 1
    except PermissionDenied as e: print(f"Error: {e}", file=sys.stderr); return 3
    except HelpdeskError as e: print(f"Error: {e}", file=sys.stderr); return 2
    except SystemExit as s: return s.code if isinstance(s.code, int) else 2
