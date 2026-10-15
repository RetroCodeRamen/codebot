"""A tiny knowledge base: articles from a JSON file next to the data file, suggested for a ticket by keyword overlap."""
import json, os
from .utils import words

STOP = {"the", "a", "an", "is", "it", "to", "and", "or", "of", "in", "on", "for", "my", "i", "not", "no", "with", "does", "do"}

class KnowledgeBase:
    def __init__(self, path):
        self.path, self.articles = path, []
        if os.path.exists(path):
            with open(path) as f: self.articles = [a for a in json.load(f) if isinstance(a, dict) and a.get("title")]

    def keywords(self, text):
        return {w for w in words(text) if w not in STOP and len(w) > 2}

    def score(self, article, ticket):
        mine = self.keywords(article["title"]) | self.keywords(article.get("body", "")) | {k.lower() for k in article.get("keywords", [])}
        theirs = self.keywords(ticket.title) | self.keywords(ticket.description)
        title_hits = len(self.keywords(article["title"]) & theirs)
        return len(mine & theirs) + 2 * title_hits

    def suggest(self, ticket, limit=3):
        """Best matching articles (score > 0), best first, ties by title."""
        scored = [(self.score(a, ticket), a) for a in self.articles]
        scored = [(s, a) for s, a in scored if s > 0]
        scored.sort(key=lambda sa: (-sa[0], sa[1]["title"]))
        return [a for _, a in scored[:limit]]
