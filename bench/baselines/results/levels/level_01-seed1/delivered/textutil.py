def slugify(text):
    if not isinstance(text, str):
        raise TypeError
    result = []
    prev_hyphen = False
    for ch in text:
        if ch.isascii() and (ch.isalpha() or ch.isdigit()):
            result.append(ch.lower())
            prev_hyphen = False
        else:
            if not prev_hyphen:
                result.append('-')
            prev_hyphen = True
    slug = ''.join(result).strip('-')
    return slug


def truncate(text, width, suffix="..."):
    if not isinstance(width, int) or isinstance(width, bool):
        raise ValueError
    if width < 0:
        raise ValueError
    if len(text) <= width:
        return text
    if width < len(suffix):
        return suffix[:width]
    return text[:width - len(suffix)] + suffix
