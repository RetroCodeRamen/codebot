import base64, sys
from common import Inputs, options, out

opts, files = options(sys.argv[1:], "d", "w")
data = Inputs("b64", files).all()
if "d" in opts: out(base64.b64decode(data.replace("\n", "")).decode("latin-1"))
else:
    text = base64.b64encode(data.encode("latin-1")).decode(); cols = int(opts.get("w", 76))
    if cols == 0: out(text)
    else: out("".join(text[i:i + cols] + "\n" for i in range(0, len(text), cols)))
