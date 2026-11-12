import sys
from common import Inputs, options, out

opts, files = options(sys.argv[1:], "", "nc")
ins = Inputs("head", files); first = True
for name, data in ins:
    if len(files) > 1:
        out(("" if first else "\n") + f"==> {name} <==\n"); first = False
    if "c" in opts: out(data[:int(opts["c"])])
    else: out("".join(data.splitlines(keepends=True)[:int(opts.get("n", 10))]))
sys.exit(1 if ins.missing else 0)
