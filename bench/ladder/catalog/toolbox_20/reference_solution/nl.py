import sys
from common import Inputs, lines, options, out

opts, files = options(sys.argv[1:], "", "b")
every = opts.get("b") == "a"; no = 0
for line in lines(Inputs("nl", files).all()):
    if line or every: no += 1; out(f"{no:6d}\t{line}\n")
    else: out(" " * 7 + "\n")
