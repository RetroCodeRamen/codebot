import sys
from common import Inputs, lines, options, out

opts, files = options(sys.argv[1:], "s", "w")
width = int(opts.get("w", 80))
for line in lines(Inputs("fold", files).all()):
    while len(line) > width:
        cut = width
        if "s" in opts:
            sp = line.rfind(" ", 0, width)
            if sp >= 0: cut = sp + 1
        out(line[:cut] + "\n"); line = line[cut:]
    out(line + "\n")
