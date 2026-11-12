import sys
from common import options, out

def chars(text):
    s, i, res = text.replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\"), 0, []
    while i < len(s):
        if i + 2 < len(s) and s[i + 1] == "-": res += [chr(c) for c in range(ord(s[i]), ord(s[i + 2]) + 1)]; i += 3
        else: res.append(s[i]); i += 1
    return res

opts, args = options(sys.argv[1:], "ds")
data = sys.stdin.buffer.read().decode("latin-1"); one = chars(args[0])
if "d" in opts: out("".join(c for c in data if c not in one))
elif "s" in opts:
    res = []
    for c in data:
        if not (res and res[-1] == c and c in one): res.append(c)
    out("".join(res))
else:
    two = chars(args[1]); table = {c: two[min(i, len(two) - 1)] for i, c in enumerate(one)}
    out("".join(table.get(c, c) for c in data))
