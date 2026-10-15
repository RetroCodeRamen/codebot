"""Integrity checks of the data set: dangling references, duplicate ids, counters that are behind."""


def check_integrity(store):
    """A list of problem descriptions (empty when the data is consistent)."""
    problems = []
    for ticket in sorted(store.tickets.values(), key=lambda t: t.id):
        if ticket.requester_id not in store.users:
            problems.append(f"ticket #{ticket.id}: unknown requester {ticket.requester_id}")
        if ticket.assignee_id is not None and ticket.assignee_id not in store.users:
            problems.append(f"ticket #{ticket.id}: unknown assignee {ticket.assignee_id}")
        for watcher in ticket.watchers:
            if watcher not in store.users:
                problems.append(f"ticket #{ticket.id}: unknown watcher {watcher}")
        for comment in ticket.comments:
            if comment.ticket_id != ticket.id:
                problems.append(f"comment {comment.id}: belongs to ticket #{comment.ticket_id} but is stored on #{ticket.id}")
            if comment.author_id not in store.users:
                problems.append(f"comment {comment.id}: unknown author {comment.author_id}")
        for link in ticket.links:
            if link["target"] not in store.tickets:
                problems.append(f"ticket #{ticket.id}: link to missing ticket #{link['target']}")
    seen = {}
    for ticket in store.tickets.values():
        for comment in ticket.comments:
            if comment.id in seen:
                problems.append(f"comment id {comment.id} is used on tickets #{seen[comment.id]} and #{ticket.id}")
            seen[comment.id] = ticket.id
    if store.tickets and store.next["ticket"] <= max(store.tickets):
        problems.append("the ticket id counter is behind the highest ticket id")
    if store.users and store.next["user"] <= max(store.users):
        problems.append("the user id counter is behind the highest user id")
    if seen and store.next["comment"] <= max(seen):
        problems.append("the comment id counter is behind the highest comment id")
    return problems


def repair(store):
    """Fix what can be fixed safely: drop dangling watchers and links, advance counters. Returns the number of changes."""
    changes = 0
    for ticket in store.tickets.values():
        kept = [w for w in ticket.watchers if w in store.users]
        changes += len(ticket.watchers) - len(kept)
        ticket.watchers = kept
        links = [l for l in ticket.links if l["target"] in store.tickets]
        changes += len(ticket.links) - len(links)
        ticket.links = links
    if store.tickets and store.next["ticket"] <= max(store.tickets):
        store.next["ticket"] = max(store.tickets) + 1
        changes += 1
    if store.users and store.next["user"] <= max(store.users):
        store.next["user"] = max(store.users) + 1
        changes += 1
    comment_ids = [c.id for t in store.tickets.values() for c in t.comments]
    if comment_ids and store.next["comment"] <= max(comment_ids):
        store.next["comment"] = max(comment_ids) + 1
        changes += 1
    if changes:
        store.save()
    return changes
