"""Business-hours arithmetic: an alternative SLA clock that only counts Monday to Friday, 09:00-17:00."""
import datetime
from .utils import ISO, parse_iso

OPEN, CLOSE = 9, 17                       # business day from 09:00 to 17:00
DAY_HOURS = CLOSE - OPEN

def is_business_day(d):
    return d.weekday() < 5

def next_open(dt):
    """The first moment at or after `dt` that lies inside business hours."""
    while True:
        if not is_business_day(dt): dt = (dt + datetime.timedelta(days=1)).replace(hour=OPEN, minute=0, second=0); continue
        if dt.hour < OPEN: return dt.replace(hour=OPEN, minute=0, second=0)
        if dt.hour >= CLOSE: dt = (dt + datetime.timedelta(days=1)).replace(hour=OPEN, minute=0, second=0); continue
        return dt

def add_business_hours(ts, hours):
    """ISO timestamp plus a number of business hours, as an ISO string."""
    dt = next_open(parse_iso(ts)); remaining = float(hours)
    while True:
        left_today = (dt.replace(hour=CLOSE, minute=0, second=0) - dt).total_seconds() / 3600.0
        if remaining <= left_today: return (dt + datetime.timedelta(hours=remaining)).strftime(ISO)
        remaining -= left_today; dt = next_open((dt + datetime.timedelta(days=1)).replace(hour=OPEN, minute=0, second=0))

def business_hours_between(start, end):
    """Business hours elapsed from start to end (0 when end is not after start)."""
    a, b = parse_iso(start), parse_iso(end)
    if b <= a: return 0.0
    total, cur = 0.0, next_open(a)
    while cur < b:
        day_end = cur.replace(hour=CLOSE, minute=0, second=0); stop = min(day_end, b); total += max(0.0, (stop - cur).total_seconds() / 3600.0)
        cur = next_open((cur + datetime.timedelta(days=1)).replace(hour=OPEN, minute=0, second=0))
    return round(total, 4)
