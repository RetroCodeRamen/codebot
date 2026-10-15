def _score(note, terms):
    title, body, tags = note.title.lower(), note.body.lower(), " ".join(note.tags)
    total = 0
    for t in terms:
        in_t, in_b, in_g = t in title, t in body, t in tags
        if not (in_t or in_b or in_g): return None
        total += 3 * in_t + 1 * in_b + 2 * in_g
    return total

def search(notes, query="", tags=()):
    terms = [t.lower() for t in str(query).split()]; need = [t.strip().lower() for t in tags]; hits = []
    for n in notes:
        if any(t not in n.tags for t in need): continue
        s = _score(n, terms) if terms else 0
        if s is not None: hits.append((n, s))
    hits.sort(key=lambda h: (-h[1], not h[0].pinned, h[0].id))
    return [n for n, _ in hits]
