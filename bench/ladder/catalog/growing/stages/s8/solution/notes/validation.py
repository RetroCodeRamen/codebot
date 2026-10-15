"""Validation of the values the user gives us."""
import re
from .errors import ValidationError

def valid_title(v):
    if not isinstance(v, str) or not v.strip(): raise ValidationError("title must not be empty")
    v = v.strip()
    if len(v) > 100 or "\n" in v: raise ValidationError("title must be one line of at most 100 characters")
    return v

def valid_body(v):
    if not isinstance(v, str): raise ValidationError("body must be a string")
    if len(v) > 5000: raise ValidationError("body must be at most 5000 characters")
    return v

TAG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,19}$")

def valid_tags(v):
    if isinstance(v, str) or not hasattr(v, "__iter__"): raise ValidationError("tags must be a list of strings")
    out = []
    for t in v:
        if not isinstance(t, str): raise ValidationError("tags must be a list of strings")
        t = t.strip().lower()
        if not TAG.match(t): raise ValidationError(f"invalid tag '{t}'")
        if t not in out: out.append(t)
    if len(out) > 10: raise ValidationError("at most 10 tags are allowed")
    return out
