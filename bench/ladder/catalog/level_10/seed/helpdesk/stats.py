"""Statistics about how tickets are handled."""
from .utils import hours_between

def percentile(values, p):
    """Linear-interpolated percentile (0-100) of a list of numbers; None for an empty list."""
    if not values: return None
    xs = sorted(values)
    if len(xs) == 1: return float(xs[0])
    k = (len(xs) - 1) * p / 100.0; lo = int(k); hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)

def first_response_hours(store):
    """Hours from creation to the first public comment by someone other than the requester, per ticket id (tickets without one are left out)."""
    out = {}
    for t in store.tickets.values():
        for c in sorted(t.comments, key=lambda c: (c.ts, c.id)):
            if c.author_id != t.requester_id and not c.internal:
                out[t.id] = round(hours_between(t.created, c.ts), 2); break
    return out

def agent_performance(store):
    """{agent id: {"assigned": n, "closed": n, "avg_close_hours": x}} for every agent that has at least one ticket."""
    out = {}
    for t in store.tickets.values():
        if t.assignee_id is None: continue
        row = out.setdefault(t.assignee_id, {"assigned": 0, "closed": 0, "hours": []})
        row["assigned"] += 1
        if t.closed_at: row["closed"] += 1; row["hours"].append(hours_between(t.created, t.closed_at))
    return {uid: {"assigned": r["assigned"], "closed": r["closed"], "avg_close_hours": round(sum(r["hours"]) / len(r["hours"]), 2) if r["hours"] else None} for uid, r in sorted(out.items())}

def summary_percentiles(store):
    """Median and 90th percentile of the first-response times."""
    v = list(first_response_hours(store).values())
    return {"count": len(v), "p50": percentile(v, 50), "p90": percentile(v, 90)}
