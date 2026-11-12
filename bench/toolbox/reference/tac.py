import sys
from common import Inputs, lines, out

for name, data in Inputs("tac", sys.argv[1:]):
    out("".join(line + "\n" for line in reversed(lines(data))))
