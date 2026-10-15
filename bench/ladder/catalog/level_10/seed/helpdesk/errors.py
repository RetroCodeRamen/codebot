"""Exception hierarchy. Every error the helpdesk raises on purpose derives from HelpdeskError."""

class HelpdeskError(Exception):
    """Base class of all helpdesk errors."""

class NotFound(HelpdeskError):
    """A user, ticket or comment does not exist."""

class PermissionDenied(HelpdeskError):
    """The acting user's role does not allow the action."""

class InvalidTransition(HelpdeskError):
    """A status change that the workflow does not allow."""

class ValidationError(HelpdeskError, ValueError):
    """Input that does not satisfy a rule (empty title, unknown priority, ...)."""

class StoreError(HelpdeskError):
    """The data file is unreadable or of an unsupported version."""
