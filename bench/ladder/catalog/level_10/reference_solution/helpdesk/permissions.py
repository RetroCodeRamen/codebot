"""Who may do what. A single table: role -> set of actions."""
from .errors import PermissionDenied

ACTIONS = {
    "admin": {"create_ticket", "comment", "internal_comment", "assign", "change_status", "change_priority", "view_all", "manage_users", "export", "report", "watch", "attach", "purge", "label", "merge", "audit"},
    "agent": {"create_ticket", "comment", "internal_comment", "assign", "change_status", "change_priority", "view_all", "export", "report", "watch", "attach", "label", "merge"},
    "customer": {"create_ticket", "comment", "watch", "attach"},
}

def can(user, action):
    return user.active and action in ACTIONS.get(user.role, set())

def require(user, action):
    """Raise PermissionDenied unless the user may perform the action."""
    if not user.active: raise PermissionDenied(f"{user.name} is disabled")
    if action not in ACTIONS.get(user.role, set()): raise PermissionDenied(f"{user.name} ({user.role}) may not {action.replace('_', ' ')}")

def matrix():
    """Plain-text table of the permissions of every role."""
    actions = sorted({a for acts in ACTIONS.values() for a in acts})
    width = max(len(a) for a in actions)
    lines = [" " * width + "  " + "  ".join(f"{r:<8}" for r in ACTIONS)]
    for action in actions:
        lines.append(f"{action:<{width}}  " + "  ".join(f"{('yes' if action in ACTIONS[r] else '-'):<8}" for r in ACTIONS))
    return "\n".join(lines)
