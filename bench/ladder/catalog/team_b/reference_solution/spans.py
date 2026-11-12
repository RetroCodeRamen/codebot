from clock import parse_time

def parse_span(text):
    if "-" not in text: raise ValueError(f"not a span: {text!r}")
    a, b = text.split("-", 1)
    start, end = parse_time(a.rstrip()), parse_time(b.lstrip())
    if end <= start: raise ValueError(f"the end is not after the start: {text!r}")
    return (start, end)

def merge(spans):
    out = []
    for start, end in sorted((s[0], s[1]) for s in spans):
        if out and start <= out[-1][1]: out[-1] = (out[-1][0], max(out[-1][1], end))
        else: out.append((start, end))
    return out
