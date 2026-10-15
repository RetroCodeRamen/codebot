"""SLA compliance figures per priority."""
from . import config
from .utils import hours_between


def compliance(store, now):
    """For every priority: how many tickets were created, how many finished on time, how many are overdue right now.

    A ticket finished on time when it was resolved or closed no later than its deadline (the closing time is used; resolved tickets
    that were never closed count with their last update). Open tickets past their deadline are overdue; the rest are still running."""
    out = {p: {"total": 0, "on_time": 0, "late": 0, "overdue": 0, "running": 0} for p in config.PRIORITIES}
    for ticket in store.tickets.values():
        row = out[ticket.priority]
        row["total"] += 1
        finished = ticket.status in ("resolved", "closed")
        if finished:
            when = ticket.closed_at or ticket.updated
            if ticket.sla_due is None or when <= ticket.sla_due:
                row["on_time"] += 1
            else:
                row["late"] += 1
        elif ticket.sla_due is not None and ticket.sla_due < now:
            row["overdue"] += 1
        else:
            row["running"] += 1
    return out


def compliance_rate(row):
    """Share of finished tickets that were on time (None when nothing has finished yet)."""
    done = row["on_time"] + row["late"]
    return None if not done else round(row["on_time"] / done, 4)


def worst_overdue(store, now, limit=5):
    """The tickets that are overdue the longest, as (ticket_id, hours_over)."""
    rows = []
    for ticket in store.tickets.values():
        if ticket.status not in ("resolved", "closed") and ticket.sla_due and ticket.sla_due < now:
            rows.append((ticket.id, round(hours_between(ticket.sla_due, now), 2)))
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows[:limit]
