import sys
from common import out

name = sys.argv[1]; suffix = sys.argv[2] if len(sys.argv) > 2 else ""
base = name.rstrip("/").rsplit("/", 1)[-1] if name.strip("/") else "/"
if suffix and base.endswith(suffix) and len(base) > len(suffix): base = base[:-len(suffix)]
out(base + "\n")
