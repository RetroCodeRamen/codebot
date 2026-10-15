"""A readable history of a ticket assembled from the data the ticket itself holds (creation, comments, attachments, closing)."""


def events(ticket, store, viewer=None, show_internal=False):
    """Chronological list of (timestamp, text) pairs. Internal comments are only included when show_internal is true."""
    def name(user_id):
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
    rows.sort(key=lambda r: r[0])
    return rows


def render(ticket, store, show_internal=False):
    """Plain text timeline, one line per event."""
    lines = [f"Timeline of ticket #{ticket.id}: {ticket.title}"]
    for ts, text in events(ticket, store, show_internal=show_internal):
        lines.append(f"  {ts}  {text}")
    return "\n".join(lines)
