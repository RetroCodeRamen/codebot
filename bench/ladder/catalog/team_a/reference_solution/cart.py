import re
from catalog import lookup, parse_catalog
from money import format_price

_ENTRY = re.compile(r"(.*?)(?:\s+x(\d+))?")

def parse_items(text):
    if not text.strip(): return []
    totals = {}
    for entry in text.split(","):
        entry = entry.strip()
        if not entry: raise ValueError("empty entry")
        m = _ENTRY.fullmatch(entry)
        name = m.group(1).strip().lower(); qty = int(m.group(2)) if m.group(2) else 1
        if not name or qty == 0: raise ValueError(f"bad entry {entry!r}")
        totals[name] = totals.get(name, 0) + qty
    return list(totals.items())

def subtotal(catalog_text, items_text):
    cat = parse_catalog(catalog_text)
    return sum(lookup(cat, name) * qty for name, qty in parse_items(items_text))

def discount(subtotal_cents, code):
    code = code.strip().upper()
    if code == "": return 0
    if code == "SAVE10": return (subtotal_cents + 5) // 10
    if code == "FIVEOFF": return 500 if subtotal_cents >= 2000 else 0
    raise ValueError(f"unknown code {code!r}")

def receipt(catalog_text, items_text, code=""):
    cat = parse_catalog(catalog_text); lines = []; sub = 0
    for name, qty in parse_items(items_text):
        unit = lookup(cat, name); sub += unit * qty
        lines.append(f"{qty} x {name} @ {format_price(unit)} = {format_price(unit * qty)}")
    off = discount(sub, code)
    lines.append(f"Subtotal: {format_price(sub)}")
    if off > 0: lines.append(f"Discount: -{format_price(off)}")
    lines.append(f"Total: {format_price(sub - off)}")
    return "\n".join(lines)
