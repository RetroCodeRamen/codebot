"""Create tickets from a CSV file (columns: title, description, priority, category, requester)."""
import csv
from .errors import HelpdeskError, NotFound

REQUIRED = ("title", "description")

def read_rows(path):
    """Rows of the CSV as dicts with stripped values; raises ValueError when a required column is missing."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing: raise ValueError(f"missing column(s): {', '.join(missing)}")
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in reader]

class Importer:
    def __init__(self, store, users, tickets):
        self.store, self.users, self.tickets = store, users, tickets

    def requester_id(self, value, default_id):
        """A requester given by id or by name; empty means the acting user."""
        if not value: return default_id
        if value.isdigit(): return self.store.user(int(value)).id
        return self.users.find(value).id

    def run(self, path, actor_id):
        """Import every row; returns (created_ids, errors) where errors is a list of 'row N: message' strings. Bad rows do not stop the import."""
        created, errors = [], []
        for n, row in enumerate(read_rows(path), start=2):
            try:
                rid = self.requester_id(row.get("requester", ""), actor_id)
                t = self.tickets.create(rid, row.get("title", ""), row.get("description", ""), row.get("priority") or "normal", row.get("category") or "general", actor_id=actor_id)
                created.append(t.id)
            except (HelpdeskError, NotFound) as e: errors.append(f"row {n}: {e}")
        return created, errors
