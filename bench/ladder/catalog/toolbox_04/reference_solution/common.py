"""What the tools share: option parsing, reading the inputs, writing bytes."""
import sys

def options(argv, flags="", valued=""):
    """({letter: True or its value}, the remaining arguments). Parsing stops at the first argument that is not made of known options."""
    opts, i = {}, 0
    while i < len(argv):
        a = argv[i]
        if len(a) < 2 or a[0] != "-" or a[1] not in flags + valued: break
        j, ok = 1, True
        while j < len(a):
            c = a[j]
            if c in valued:
                v = a[j + 1:]
                if v == "" and i + 1 < len(argv): i += 1; v = argv[i]
                opts[c] = v; break
            if c not in flags: ok = False; break
            opts[c] = True; j += 1
        if not ok: break
        i += 1
    return opts, argv[i:]

class Inputs:
    """The text of each FILE (or of standard input when there is none); a missing file is reported and remembered in .missing."""
    def __init__(self, tool, files): self.tool, self.files, self.missing = tool, files, False
    def __iter__(self):
        if not self.files: yield None, sys.stdin.buffer.read().decode("latin-1"); return
        for name in self.files:
            try:
                with open(name, "rb") as f: data = f.read().decode("latin-1")
            except OSError:
                sys.stderr.write(f"{self.tool}: {name}: No such file or directory\n"); self.missing = True; continue
            yield name, data
    def all(self): return "".join(d for _, d in self)

def lines(text):
    """The lines of a text without their newlines (a last line without one counts)."""
    out = text.split("\n")
    return out[:-1] if out[-1] == "" else out

def out(text):
    sys.stdout.buffer.write(text.encode("latin-1")); sys.stdout.buffer.flush()
