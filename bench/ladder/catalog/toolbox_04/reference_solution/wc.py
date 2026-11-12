import sys
from common import Inputs, options, out

opts, files = options(sys.argv[1:], "lwc")
want = [k for k in "lwc" if k in opts] or list("lwc")
ins = Inputs("wc", files); rows = []
for name, data in ins:
    rows.append(({"l": data.count("\n"), "w": len(data.split()), "c": len(data)}, name))
if len(files) > 1: rows.append(({k: sum(r[0][k] for r in rows) for k in "lwc"}, "total"))
if len(want) == 1 and len(files) <= 1: width = 1
elif not files: width = 7
else: width = len(str(sum(r[0]["c"] for r in rows if r[1] != "total")))
for counts, name in rows:
    out(" ".join(f"{counts[k]:>{width}}" for k in want) + (f" {name}" if name else "") + "\n")
sys.exit(1 if ins.missing else 0)
