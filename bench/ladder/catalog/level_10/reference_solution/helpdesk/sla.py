"""Service level deadlines. A ticket is overdue when it is not closed and its SLA time has passed."""
from . import config
from .utils import add_hours, hours_between

def hours_for(priority):
    return config.SLA_HOURS[priority]

def due_for(priority, created):
    """Deadline (ISO) of a ticket with this priority created at `created`."""
    return add_hours(created, hours_for(priority))

def hours_for_severity(severity):
    return hours_for(config.SEVERITY_TO_PRIORITY[severity])

def due_for_severity(severity, created):
    """Deadline (ISO) of a ticket with this severity created at `created`."""
    return add_hours(created, hours_for_severity(severity))

def breached(ticket, now):
    return ticket.status not in ("resolved", "closed") and ticket.sla_due is not None and ticket.sla_due < now

def remaining_hours(ticket, now):
    """Hours until the deadline (negative when overdue); None without a deadline."""
    return None if ticket.sla_due is None else hours_between(now, ticket.sla_due)
