import sys
from common import Inputs, options, out

opts, files = options(sys.argv[1:], "n")
ins = Inputs("cat", files); no = 0
for name, data in ins:
    if "n" not in opts: out(data); continue
    for line in data.splitlines(keepends=True):
        no += 1; out(f"{no:6d}\t{line}")
sys.exit(1 if ins.missing else 0)
