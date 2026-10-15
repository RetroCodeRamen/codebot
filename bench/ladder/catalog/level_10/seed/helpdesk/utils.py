"""Small helpers: timestamps, validation, text formatting."""
import datetime, re, time
from .errors import ValidationError

ISO = "%Y-%m-%dT%H:%M:%S"

def system_clock():
    """Current local time as an ISO string (the default clock of every service)."""
    return time.strftime(ISO)

def parse_iso(ts):
    """ISO string -> datetime. Raises ValidationError for anything else."""
    try: return datetime.datetime.strptime(ts, ISO)
    except (TypeError, ValueError): raise ValidationError(f"invalid timestamp {ts!r}")

def add_hours(ts, hours):
    """ISO timestamp plus a number of hours, as an ISO string."""
    return (parse_iso(ts) + datetime.timedelta(hours=hours)).strftime(ISO)

def hours_between(start, end):
    """Hours from start to end (negative when end is earlier)."""
    return (parse_iso(end) - parse_iso(start)).total_seconds() / 3600.0

def require_text(value, name, maximum):
    """A stripped, non-empty string of at most `maximum` characters."""
    if not isinstance(value, str): raise ValidationError(f"{name} must be a string")
    v = value.strip()
    if not v: raise ValidationError(f"{name} must not be empty")
    if len(v) > maximum: raise ValidationError(f"{name} must be at most {maximum} characters")
    return v

def require_choice(value, name, choices):
    if value not in choices: raise ValidationError(f"invalid {name} '{value}' (choose from {', '.join(choices)})")
    return value

def require_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int): raise ValidationError(f"{name} must be an integer")
    return value

def plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")

def table(rows, headers):
    """Plain-text table: columns left-aligned and padded to the widest cell."""
    cells = [[str(c) for c in r] for r in rows]; widths = [len(h) for h in headers]
    for r in cells:
        for i, c in enumerate(r): widths[i] = max(widths[i], len(c))
    line = lambda r: "  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip()
    return "\n".join([line(headers)] + [line(r) for r in cells])

WORD = re.compile(r"[A-Za-z0-9_]+")

def words(text):
    """Lowercase words of a text (letters, digits and underscore)."""
    return [w.lower() for w in WORD.findall(text or "")]
