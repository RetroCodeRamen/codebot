import sys
from common import lines, options, out

opts, files = options(sys.argv[1:], "s", "d")
delim = opts.get("d", "\t"); cols = []
for name in files:
    with open(name, "rb") as f: cols.append(lines(f.read().decode("latin-1")))
if "s" in opts:
    for col in cols: out(delim.join(col) + "\n")
else:
    for i in range(max((len(c) for c in cols), default=0)):
        out(delim.join(c[i] if i < len(c) else "" for c in cols) + "\n")
