"""Canned replies with placeholders: {requester}, {id}, {title}, {assignee}, {agent}."""
from .errors import NotFound, ValidationError

TEMPLATES = {
    "received": "Hello {requester}, we received your request #{id} ({title}) and will get back to you soon. -- {agent}",
    "need-info": "Hello {requester}, to continue with ticket #{id} we need more information. Please reply with details. -- {agent}",
    "resolved": "Hello {requester}, we believe ticket #{id} ({title}) is resolved. Reply within 3 days if it is not. -- {agent}",
    "closing": "Hello {requester}, ticket #{id} is being closed because we did not hear from you. -- {agent}",
}

def names():
    return sorted(TEMPLATES)

def render(name, ticket, store, agent):
    """Fill a template for a ticket; `agent` is the acting User."""
    if name not in TEMPLATES: raise NotFound(f"no template named '{name}' (templates: {', '.join(names())})")
    requester = store.users.get(ticket.requester_id); assignee = store.users.get(ticket.assignee_id) if ticket.assignee_id else None
    values = {"requester": requester.name if requester else "customer", "id": ticket.id, "title": ticket.title, "assignee": assignee.name if assignee else "unassigned", "agent": agent.name}
    try: return TEMPLATES[name].format(**values)
    except (KeyError, IndexError) as e: raise ValidationError(f"template '{name}' uses an unknown placeholder {e}")

class Replier:
    def __init__(self, store, tickets):
        self.store, self.tickets = store, tickets

    def reply(self, ticket_id, name, actor_id, internal=False):
        """Post a rendered template as a comment of the acting user."""
        actor = self.store.user(actor_id); ticket = self.store.ticket(ticket_id)
        return self.tickets.add_comment(ticket_id, actor_id, render(name, ticket, self.store, actor), internal)
