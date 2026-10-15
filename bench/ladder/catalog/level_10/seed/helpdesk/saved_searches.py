"""Saved searches: a user can store a query under a name and run it again later. Stored in the data file under `saved_searches`."""
import re
from . import search as searchmod
from .errors import NotFound, ValidationError

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,29}$")

class SavedSearches:
    def __init__(self, store, tickets):
        self.store, self.tickets = store, tickets

    def _mine(self, user_id):
        return [s for s in self.store.saved_searches if s["user_id"] == user_id]

    def save(self, user_id, name, query):
        self.store.user(user_id)
        if not NAME.match(name or ""): raise ValidationError("a saved search name is 1-30 characters of a-z, 0-9, _ and -")
        searchmod.parse_query(query)                              # reject broken queries now, not when they are run
        for s in self._mine(user_id):
            if s["name"] == name: s["query"] = query; self.store.save(); return s
        s = {"user_id": user_id, "name": name, "query": query}; self.store.saved_searches.append(s); self.store.save(); return s

    def list(self, user_id):
        return sorted(self._mine(user_id), key=lambda s: s["name"])

    def delete(self, user_id, name):
        for s in self._mine(user_id):
            if s["name"] == name: self.store.saved_searches.remove(s); self.store.save(); return s
        raise NotFound(f"no saved search '{name}'")

    def run(self, user_id, name):
        """Run a saved search over the tickets the user may see."""
        for s in self._mine(user_id):
            if s["name"] == name: return searchmod.search(self.tickets.list(user_id), s["query"])
        raise NotFound(f"no saved search '{name}'")
