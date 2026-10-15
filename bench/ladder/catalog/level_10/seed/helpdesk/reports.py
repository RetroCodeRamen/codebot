"""Aggregations over the whole data set."""
from . import sla
from .utils import hours_between

def summary(store, now):
    """Counts by status, priority and category, plus the number of open tickets and their average age in hours."""
    tickets = list(store.tickets.values()); out = {"total": len(tickets), "by_status": {}, "by_priority": {}, "by_category": {}}
    for t in tickets:
        for key, value in (("by_status", t.status), ("by_priority", t.priority), ("by_category", t.category)): out[key][value] = out[key].get(value, 0) + 1
    open_ = [t for t in tickets if t.status != "closed"]
    out["open_count"] = len(open_)
    out["avg_open_age_hours"] = round(sum(hours_between(t.created, now) for t in open_) / len(open_), 2) if open_ else 0.0
    return out

def sla_breaches(store, now):
    """Overdue tickets, most overdue first."""
    late = [t for t in store.tickets.values() if sla.breached(t, now)]
    return sorted(late, key=lambda t: (t.sla_due, t.id))

def workload(store):
    """Number of not-yet-closed tickets per assignee id."""
    counts = {}
    for t in store.tickets.values():
        if t.assignee_id is not None and t.status != "closed": counts[t.assignee_id] = counts.get(t.assignee_id, 0) + 1
    return dict(sorted(counts.items()))

def resolution_times(store):
    """Hours from creation to closing for every closed ticket, keyed by ticket id."""
    return {t.id: round(hours_between(t.created, t.closed_at), 2) for t in store.tickets.values() if t.closed_at}
