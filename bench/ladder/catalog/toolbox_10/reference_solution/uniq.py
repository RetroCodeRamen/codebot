import sys
from common import Inputs, lines, options, out

opts, files = options(sys.argv[1:], "cdui")
key = (lambda s: s.lower()) if "i" in opts else (lambda s: s)
runs = []
for line in lines(Inputs("uniq", files).all()):
    if runs and key(runs[-1][0]) == key(line): runs[-1][1] += 1
    else: runs.append([line, 1])
for line, n in runs:
    if "d" in opts and n < 2: continue
    if "u" in opts and n > 1: continue
    out((f"{n:7d} " if "c" in opts else "") + line + "\n")
