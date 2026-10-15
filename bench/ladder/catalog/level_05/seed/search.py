"""Searching the catalogue."""

def search_books(books, text):
    """Books whose title contains `text`, ordered by title. An empty search matches every book."""
    hits = [b for b in books if text in b.title]
    return sorted(hits, key=lambda b: b.title)
