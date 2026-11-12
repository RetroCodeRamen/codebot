import re
from clock import format_time, parse_duration, parse_time
from spans import merge, parse_span

_LINE = re.compile(r"([0-9]{2}:[0-9]{2}\s*-\s*[0-9]{2}:[0-9]{2})(?:\s+.*)?")

def busy(schedule_text):
    found = []
    for line in schedule_text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"): continue
        m = _LINE.fullmatch(s)
        if not m: raise ValueError(f"not an appointment: {line!r}")
        found.append(parse_span(m.group(1)))
    return merge(found)

def _gaps(schedule_text, day_start, day_end, duration):
    start, end = parse_time(day_start), parse_time(day_end)
    if end <= start: raise ValueError("the day ends before it starts")
    need = parse_duration(duration); gaps = []; at = start
    for a, b in busy(schedule_text):
        a, b = max(a, start), min(b, end)
        if a >= b: continue
        if a - at >= need: gaps.append((at, a))
        at = max(at, b)
    if end - at >= need: gaps.append((at, end))
    return gaps, need

def free_slots(schedule_text, day_start, day_end, duration):
    return [f"{format_time(a)}-{format_time(b)}" for a, b in _gaps(schedule_text, day_start, day_end, duration)[0]]

def first_slot(schedule_text, day_start, day_end, duration):
    gaps, need = _gaps(schedule_text, day_start, day_end, duration)
    return f"{format_time(gaps[0][0])}-{format_time(gaps[0][0] + need)}" if gaps else None
