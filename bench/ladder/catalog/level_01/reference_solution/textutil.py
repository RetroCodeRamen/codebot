def slugify(text):
    if not isinstance(text, str): raise TypeError("text must be a str")
    out, pending = [], False
    for ch in text:
        o = ord(ch)
        if 48 <= o <= 57 or 65 <= o <= 90 or 97 <= o <= 122:
            if pending and out: out.append("-")
            pending = False; out.append(ch.lower())
        else: pending = True
    return "".join(out)

def truncate(text, width, suffix="..."):
    if width < 0: raise ValueError("width must not be negative")
    if len(text) <= width: return text
    if width < len(suffix): return suffix[:width]
    return text[:width - len(suffix)] + suffix
