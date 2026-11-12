import sys
from common import Inputs, lines, options, out

opts, args = options(sys.argv[1:], "ivcnx")
pat, files = args[0], args[1:]
fold = (lambda s: s.lower()) if "i" in opts else (lambda s: s)
def hit(line):
    found = fold(line) == fold(pat) if "x" in opts else fold(pat) in fold(line)
    return found != ("v" in opts)
any_hit = False; ins = Inputs("grep", files)
for name, data in ins:
    pre = f"{name}:" if len(files) > 1 else ""; n = 0
    for no, line in enumerate(lines(data), 1):
        if not hit(line): continue
        n += 1; any_hit = True
        if "c" not in opts: out(pre + (f"{no}:" if "n" in opts else "") + line + "\n")
    if "c" in opts: out(f"{pre}{n}\n")
sys.exit(2 if ins.missing else 0 if any_hit else 1)
