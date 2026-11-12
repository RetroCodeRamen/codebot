import sys
from common import Inputs, options, out

opts, files = options(sys.argv[1:], "", "nc")
data = Inputs("tail", files).all()
if "c" in opts:
    n = int(opts["c"]); out(data[-n:] if n else "")
else:
    rows = data.splitlines(keepends=True); n = opts.get("n", "10")
    if n.startswith("+"): out("".join(rows[max(int(n) - 1, 0):]))
    else: out("".join(rows[-int(n):]) if int(n) else "")
