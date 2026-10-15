"""A readable history of a ticket assembled from the data the ticket itself holds (creation, comments, attachments, closing)."""


def events(ticket, store, viewer=None, show_internal=False):
    """Chronological list of (timestamp, text) pairs. Internal comments are only included when show_internal is true."""
    def name(user_id):
        if user_id is None:
            return "system"
        user = store.users.get(user_id)
        return user.name if user else f"user #{user_id}"

    rows = [(ticket.created, f"created by {name(ticket.requester_id)} with priority {ticket.priority}")]
    for comment in ticket.comments:
        if comment.internal and not show_internal:
            continue
        kind = "internal note" if comment.internal else "comment"
        rows.append((comment.ts, f"{kind} by {name(comment.author_id)}: {comment.body}"))
    for attachment in ticket.attachments:
        rows.append((attachment["ts"], f"{name(attachment['added_by'])} attached {attachment['name']} ({attachment['size']} bytes)"))
    if ticket.closed_at:
        rows.append((ticket.closed_at, "closed" + (f" ({ticket.resolution})" if ticket.resolution else "")))
    for entry in getattr(store, "audit", []):
        if entry["entity"] != "ticket" or entry["entity_id"] != ticket.id or entry["outcome"] != "ok":
            continue
        who = name(entry["actor"])
        ch = entry["changes"]
        if entry["action"] == "ticket.assign" and "assignee_id" in ch:
            rows.append((entry["ts"], f"assigned to {name(ch['assignee_id'][1])} by {who}"))
        elif entry["action"] == "ticket.status" and ch["status"][1] != "closed":              # closing is already shown from `closed_at`
            rows.append((entry["ts"], f"status changed from {ch['status'][0]} to {ch['status'][1]} by {who}"))
        elif entry["action"] == "ticket.severity":
            rows.append((entry["ts"], f"severity changed from {ch['severity'][0]} to {ch['severity'][1]} by {who}"))
        elif entry["action"] in ("ticket.label_add", "ticket.label_remove"):
            old, new = ch["labels"]
            for label in [l for l in new if l not in old]:
                rows.append((entry["ts"], f"label {label} added by {who}"))
            for label in [l for l in old if l not in new]:
                rows.append((entry["ts"], f"label {label} removed by {who}"))
        elif entry["action"] == "ticket.merge":
            rows.append((entry["ts"], f"merged into #{ch['merged_into'][1]} by {who}"))
    rows.sort(key=lambda r: r[0])
    return rows


def render(ticket, store, show_internal=False):
    """Plain text timeline, one line per event."""
    lines = [f"Timeline of ticket #{ticket.id}: {ticket.title}"]
    for ts, text in events(ticket, store, show_internal=show_internal):
        lines.append(f"  {ts}  {text}")
    return "\n".join(lines)
