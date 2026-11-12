import sys
from common import Inputs, lines, options, out

opts, files = options(sys.argv[1:], "", "t")
stop = int(opts.get("t", 8))
for line in lines(Inputs("expand", files).all()):
    res = ""
    for ch in line:
        res += " " * (stop - len(res) % stop) if ch == "\t" else ch
    out(res + "\n")
