"""The ticket life cycle: which status may follow which."""
from .errors import InvalidTransition, ValidationError

TRANSITIONS = {
    "open": {"in_progress", "waiting", "resolved", "closed"},
    "in_progress": {"open", "waiting", "resolved", "closed"},
    "waiting": {"open", "in_progress", "resolved", "closed"},
    "resolved": {"open", "closed"},
    "closed": {"open"},
}

def allowed(status):
    """Statuses reachable from `status`, sorted."""
    return sorted(TRANSITIONS[status])

def check_transition(ticket, new_status, resolution=None):
    if new_status == ticket.status: raise InvalidTransition(f"ticket #{ticket.id} is already {ticket.status}")
    if new_status not in TRANSITIONS[ticket.status]:
        raise InvalidTransition(f"ticket #{ticket.id} cannot go from {ticket.status} to {new_status} (allowed: {', '.join(allowed(ticket.status))})")
    if new_status == "resolved" and not (resolution or "").strip(): raise ValidationError("a resolution text is required to resolve a ticket")
