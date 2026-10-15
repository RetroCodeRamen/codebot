"""The audit log: an append-only record of who changed what. Entries live in the data file (`audit`) and are saved together with the change they describe."""
from .errors import PermissionDenied

FIELDS = ("seq", "ts", "actor", "action", "entity", "entity_id", "outcome", "changes", "reason")


def diff(old, new):
    """{field: [old, new]} for every key whose value differs between two dicts."""
    return {k: [old.get(k), new.get(k)] for k in new if old.get(k) != new.get(k)}


class AuditLog:
    def __init__(self, store, clock):
        self.store, self.clock = store, clock

    def _next_seq(self):
        return self.store.audit[-1]["seq"] + 1 if self.store.audit else 1

    def record(self, actor, action, entity, entity_id, changes=None, outcome="ok", reason=None):
        """Append one entry (the caller saves the store). `changes` maps field -> [old, new]."""
        entry = {"seq": self._next_seq(), "ts": self.clock(), "actor": actor, "action": action, "entity": entity, "entity_id": entity_id,
                 "outcome": outcome, "changes": {k: list(v) for k, v in (changes or {}).items()}, "reason": reason}
        self.store.audit.append(entry)
        return dict(entry)

    def deny(self, actor, action, entity, entity_id, error):
        """Record a refused operation (outcome 'denied') and save it at once, because the caller is about to raise."""
        entry = self.record(actor, action, entity, entity_id, {}, "denied", str(error))
        self.store.save()
        return entry

    def guard(self, user, permission, action, entity, entity_id, permissions):
        """permissions.require(user, permission), auditing a refusal under `action`."""
        try:
            permissions.require(user, permission)
        except PermissionDenied as error:
            self.deny(user.id, action, entity, entity_id, error)
            raise

    def entries(self, entity=None, entity_id=None, actor=None, action=None, outcome=None, since=None):
        out = []
        for e in self.store.audit:
            if entity is not None and e["entity"] != entity:
                continue
            if entity_id is not None and e["entity_id"] != entity_id:
                continue
            if actor is not None and e["actor"] != actor:
                continue
            if action is not None and e["action"] != action:
                continue
            if outcome is not None and e["outcome"] != outcome:
                continue
            if since is not None and e["ts"] < since:
                continue
            out.append({**e, "changes": {k: list(v) for k, v in e["changes"].items()}})
        return out

    def count_by_action(self):
        counts = {}
        for e in self.store.audit:
            counts[e["action"]] = counts.get(e["action"], 0) + 1
        return dict(sorted(counts.items()))
