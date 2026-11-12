from money import parse_price

def parse_catalog(text):
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"): continue
        if ":" not in s: raise ValueError(f"no colon in {line!r}")
        name, price = s.split(":", 1)
        name = name.strip().lower()
        if not name: raise ValueError(f"no name in {line!r}")
        if name in out: raise ValueError(f"{name!r} appears twice")
        out[name] = parse_price(price)
    return out

def lookup(catalog, name):
    return catalog[name.strip().lower()]
