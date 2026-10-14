import re


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_kv(line):
    out = {}
    for item in line.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        k, v = (p.strip() for p in item.split("=", 1))
        try:
            out[k] = int(v)
        except ValueError:
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
    return out
