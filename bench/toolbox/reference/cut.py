import sys
from common import Inputs, lines, options, out

def ranges(text):
    out_ = []
    for part in text.split(","):
        lo, dash, hi = part.partition("-")
        out_.append((int(lo) if lo else 1, (int(hi) if hi else 10 ** 9) if dash else int(lo)))
    return out_

opts, files = options(sys.argv[1:], "s", "dfc")
sel = ranges(opts.get("f") or opts["c"]); picked = lambda n: any(lo <= n <= hi for lo, hi in sel)
delim = opts.get("d", "\t")
for line in lines(Inputs("cut", files).all()):
    if "c" in opts: out("".join(ch for n, ch in enumerate(line, 1) if picked(n)) + "\n")
    elif delim not in line:
        if "s" not in opts: out(line + "\n")
    else: out(delim.join(f for n, f in enumerate(line.split(delim), 1) if picked(n)) + "\n")
