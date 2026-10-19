import re


def slugify(text):
    if not isinstance(text, str):
        raise TypeError
    if not text:
        return ""
    result = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower())
    result = result.strip("-")
    return result


def truncate(text, width, suffix="..."):
    if not isinstance(width, int) or isinstance(width, bool):
        raise ValueError
    if len(text) <= width:
        return text
    if width < len(suffix):
        return suffix[:width]
    return text[:width - len(suffix)] + suffix
