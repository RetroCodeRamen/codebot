import re

_PRICE = re.compile(r"\$?(\d+)(?:\.(\d{1,2}))?")

def parse_price(text):
    m = _PRICE.fullmatch(text.strip()) if isinstance(text, str) else None
    if not m: raise ValueError(f"not a price: {text!r}")
    cents = m.group(2) or ""
    return int(m.group(1)) * 100 + int((cents + "00")[:2])

def format_price(cents):
    if type(cents) is not int: raise TypeError("cents must be an int")
    sign = "-" if cents < 0 else ""
    dollars, rest = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{rest:02d}"
