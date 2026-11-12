import sys
from common import out

for name in sys.argv[1:]:
    s = name.rstrip("/")
    if not s: out("/\n"); continue
    if "/" not in s: out(".\n"); continue
    out((s.rsplit("/", 1)[0].rstrip("/") or "/") + "\n")
