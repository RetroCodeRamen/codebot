"""A small rules engine. Rules live in a JSON file next to the data file (`<data file>.rules.json`):

    [{"name": "billing is at least normal", "when": {"category": "billing", "priority": "low"}, "then": {"priority": "normal", "assign": "Bob"}}]

`when` compares ticket fields for equality (category, priority, status); `then` may set the priority and assign the ticket to a user by name.
Rules are applied in file order; a later rule sees the changes of the earlier ones."""
import json
import os

from .errors import NotFound, ValidationError

CONDITION_FIELDS = ("category", "priority", "status")
ACTION_FIELDS = ("priority", "assign")


def load_rules(path):
    """The list of rules in the file (empty when the file does not exist). Raises ValidationError for malformed rules."""
    if not os.path.exists(path):
        return []
    try:
        with open(path) as handle:
            rules = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValidationError(f"cannot read rules {path}: {exc}")
    if not isinstance(rules, list):
        raise ValidationError("rules must be a JSON list")
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict) or not isinstance(rule.get("when"), dict) or not isinstance(rule.get("then"), dict):
            raise ValidationError(f"rule {index} needs 'when' and 'then' objects")
        for key in rule["when"]:
            if key not in CONDITION_FIELDS:
                raise ValidationError(f"rule {index}: unknown condition '{key}'")
        for key in rule["then"]:
            if key not in ACTION_FIELDS:
                raise ValidationError(f"rule {index}: unknown action '{key}'")
    return rules


def matches(rule, ticket):
    return all(getattr(ticket, field) == value for field, value in rule["when"].items())


class RuleEngine:
    def __init__(self, store, users, tickets, path):
        self.store = store
        self.users = users
        self.tickets = tickets
        self.path = path

    def rules(self):
        return load_rules(self.path)

    def apply(self, ticket_id, actor_id):
        """Apply every matching rule to a ticket. Returns a list of strings that describe what was done."""
        done = []
        for rule in self.rules():
            ticket = self.store.ticket(ticket_id)
            if not matches(rule, ticket):
                continue
            label = rule.get("name", "unnamed rule")
            priority = rule["then"].get("priority")
            if priority is not None and priority != ticket.priority:
                before = ticket.priority
                self.tickets.set_priority(ticket_id, priority, actor_id)
                done.append(f"{label}: priority {before} -> {priority}")
            target = rule["then"].get("assign")
            if target is not None:
                try:
                    user = self.users.find(target)
                except NotFound:
                    raise ValidationError(f"rule '{label}' assigns to unknown user '{target}'")
                if self.store.ticket(ticket_id).assignee_id != user.id:
                    self.tickets.assign(ticket_id, user.id, actor_id)
                    done.append(f"{label}: assigned to {user.name}")
        return done
