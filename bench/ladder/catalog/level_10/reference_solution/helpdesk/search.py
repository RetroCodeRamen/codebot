"""Free-text and field search. Query syntax: `status:open assignee:3 priority:high login error` (field filters plus words that must all appear)."""
from . import config
from .errors import ValidationError
from .utils import words

FIELDS = ("status", "assignee", "requester", "priority", "severity", "category", "label")

class Query:
    def __init__(self, filters, terms):
        self.filters, self.terms = filters, terms

    def __repr__(self): return f"Query({self.filters!r}, {self.terms!r})"

def parse_query(text):
    filters, terms = {}, []
    for tok in str(text).split():
        if ":" in tok:
            key, _, value = tok.partition(":"); key = key.lower()
            if key not in FIELDS: raise ValidationError(f"unknown search field '{key}' (fields: {', '.join(FIELDS)})")
            if not value: raise ValidationError(f"empty value for '{key}'")
            if key == "label": filters.setdefault("label", []).append(value.lower())          # repeated label: filters all required
            else: filters[key] = value.lower()
        else: terms.append(tok.lower())
    return Query(filters, terms)

def matches(ticket, query):
    f = query.filters
    if "status" in f and ticket.status != f["status"]: return False
    if "priority" in f and ticket.priority != f["priority"]: return False
    if "severity" in f and ticket.severity != f["severity"]: return False
    if "label" in f and not all(l in ticket.labels for l in f["label"]): return False
    if "category" in f and ticket.category != f["category"]: return False
    if "assignee" in f and str(ticket.assignee_id) != f["assignee"]: return False
    if "requester" in f and str(ticket.requester_id) != f["requester"]: return False
    if query.terms:
        haystack = set(words(ticket.title)) | set(words(ticket.description)) | {w for c in ticket.comments if not c.internal for w in words(c.body)}
        if not all(t in haystack for t in query.terms): return False
    return True

def search(tickets, text, limit=None):
    """Matching tickets, newest update first (then by id)."""
    q = parse_query(text)
    hits = sorted((t for t in tickets if matches(t, q)), key=lambda t: (t.updated, t.id), reverse=True)
    return hits[:limit] if limit else hits
