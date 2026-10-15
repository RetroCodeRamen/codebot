"""A plain-text digest of one user's unread notifications, grouped by ticket."""
def build_digest(store, notifier, user_id):
    user = store.user(user_id); items = notifier.unread(user_id)
    if not items: return f"Digest for {user.name}: nothing new."
    by_ticket = {}
    for n in items: by_ticket.setdefault(n["ticket_id"], []).append(n)
    lines = [f"Digest for {user.name}: {len(items)} new notification(s) on {len(by_ticket)} ticket(s)"]
    for tid in sorted(by_ticket):
        t = store.tickets.get(tid); lines.append(f"Ticket #{tid}" + (f" {t.title}" if t else ""))
        for n in sorted(by_ticket[tid], key=lambda n: n["id"]): lines.append(f"  - {n['message']}")
    return "\n".join(lines)
