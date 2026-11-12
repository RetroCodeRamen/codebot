import re

_TIME = re.compile(r"([0-9]{2}):([0-9]{2})")
_FORMS = (re.compile(r"([0-9]+)h([0-9]+)m"), re.compile(r"([0-9]+)h"), re.compile(r"([0-9]+)m?"))

def parse_time(text):
    m = _TIME.fullmatch(text) if isinstance(text, str) else None
    if not m: raise ValueError(f"not a time: {text!r}")
    h, mi = int(m.group(1)), int(m.group(2))
    if (h, mi) == (24, 0): return 1440
    if h > 23 or mi > 59: raise ValueError(f"not a time: {text!r}")
    return h * 60 + mi

def format_time(minutes):
    if type(minutes) is not int: raise TypeError("minutes must be an int")
    if not 0 <= minutes <= 1440: raise ValueError("minutes out of range")
    return f"{minutes // 60:02d}:{minutes % 60:02d}"

def parse_duration(text):
    s = text.strip()
    m = _FORMS[0].fullmatch(s)
    if m:
        if int(m.group(2)) >= 60: raise ValueError(f"not a duration: {text!r}")
        total = int(m.group(1)) * 60 + int(m.group(2))
    elif _FORMS[1].fullmatch(s): total = int(s[:-1]) * 60
    elif _FORMS[2].fullmatch(s): total = int(s.rstrip("m"))
    else: raise ValueError(f"not a duration: {text!r}")
    if total == 0: raise ValueError("a duration cannot be zero")
    return total
