import re, sys
from fractions import Fraction
from common import Inputs, lines, options, out

NUM = re.compile(r"\s*(-?\d+(?:\.\d*)?|-?\.\d+)")

def number(line):
    m = NUM.match(line)
    return Fraction(m.group(1)) if m else Fraction(0)

opts, files = options(sys.argv[1:], "rnuf")
key = number if "n" in opts else (lambda s: s.upper()) if "f" in opts else (lambda s: s)
rows = lines(Inputs("sort", files).all()); rev = "r" in opts
if "u" in opts:
    rows = sorted(rows, key=key, reverse=rev); seen, kept = set(), []
    for r in rows:
        if key(r) not in seen: seen.add(key(r)); kept.append(r)
    rows = kept
else: rows = sorted(rows, key=lambda s: (key(s), s), reverse=rev)
out("".join(r + "\n" for r in rows))
