"""Export of tickets for other tools."""
import csv, json, os, tempfile

CSV_COLUMNS = ["id", "title", "status", "priority", "category", "requester", "assignee", "created", "updated", "sla_due"]     # existing consumers read these ten, in this order
CSV_EXTRA = ["severity", "labels", "merged_into"]                                                                          # new columns are only ever appended

def _name(store, user_id):
    u = store.users.get(user_id)
    return u.name if u else ""

def csv_rows(store):
    for t in sorted(store.tickets.values(), key=lambda t: t.id):
        yield [t.id, t.title, t.status, t.priority, t.category, _name(store, t.requester_id), _name(store, t.assignee_id), t.created, t.updated, t.sla_due or "",
               t.severity, "|".join(t.labels), "" if t.merged_into is None else t.merged_into]

def _atomic(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".export-")
    with os.fdopen(fd, "w", newline="") as f: f.write(text)
    os.replace(tmp, path)

def export_csv(store, path):
    """Write all tickets as CSV; returns the number of tickets."""
    import io
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(CSV_COLUMNS + CSV_EXTRA); rows = list(csv_rows(store)); w.writerows(rows)
    _atomic(path, buf.getvalue()); return len(rows)

def export_json(store, path):
    """Write {"tickets": [...]} with every ticket field; returns the number of tickets."""
    tickets = [{**t.to_dict(), "priority": t.priority} for t in sorted(store.tickets.values(), key=lambda t: t.id)]       # old key kept next to the new `severity`
    _atomic(path, json.dumps({"tickets": tickets}, indent=1)); return len(tickets)
