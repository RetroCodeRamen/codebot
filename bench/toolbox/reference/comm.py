import sys
from common import lines, options, out

opts, files = options(sys.argv[1:], "123")
def read(name):
    with open(name, "rb") as f: return lines(f.read().decode("latin-1"))
a, b = read(files[0]), read(files[1]); i = j = 0
def show(col, line):
    if str(col) in opts: return
    out("\t" * sum(1 for c in range(1, col) if str(c) not in opts) + line + "\n")
while i < len(a) or j < len(b):
    if j >= len(b) or (i < len(a) and a[i] < b[j]): show(1, a[i]); i += 1
    elif i >= len(a) or b[j] < a[i]: show(2, b[j]); j += 1
    else: show(3, a[i]); i += 1; j += 1
