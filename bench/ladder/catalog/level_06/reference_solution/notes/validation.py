import re

class ValidationError(ValueError):
    pass

TAG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,19}$")

def clean_title(title):
    if not isinstance(title, str): raise ValidationError("title must be a string")
    t = title.strip()
    if not t: raise ValidationError("title must not be empty")
    if "\n" in t or "\r" in t: raise ValidationError("title must be a single line")
    if len(t) > 80: raise ValidationError("title must be at most 80 characters")
    return t

def clean_body(body):
    if not isinstance(body, str): raise ValidationError("body must be a string")
    if len(body) > 10000: raise ValidationError("body must be at most 10000 characters")
    return body

def clean_tags(tags):
    if isinstance(tags, str) or not hasattr(tags, "__iter__"): raise ValidationError("tags must be a list of strings")
    out = []
    for t in tags:
        if not isinstance(t, str): raise ValidationError("tags must be a list of strings")
        t = t.strip().lower()
        if not TAG.match(t): raise ValidationError(f"invalid tag '{t}'")
        if t not in out: out.append(t)
    if len(out) > 10: raise ValidationError("at most 10 tags are allowed")
    return out
