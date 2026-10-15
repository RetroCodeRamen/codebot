"""Searching the catalogue."""

def search_books(books, text):
    """Books whose title contains `text`, ordered by title. An empty search matches every book."""
    needle = text.lower()
    hits = [b for b in books if needle in b.title.lower()]
    return sorted(hits, key=lambda b: b.title)
