import sys
from common import Inputs, lines, out

for line in lines(Inputs("rev", sys.argv[1:]).all()): out(line[::-1] + "\n")
