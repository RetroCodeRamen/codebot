import re

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
INT = re.compile(r"^[+-]?[0-9]+$")
FLOAT = re.compile(r"^[+-]?(?:[0-9]+\.[0-9]+(?:[eE][+-]?[0-9]+)?|[0-9]+[eE][+-]?[0-9]+)$")
BOOLS = {"true": True, "yes": True, "on": True, "false": False, "no": False, "off": False}
TAIL_OK = re.compile(r"^[ \t]*(?:[#;].*)?$")
COMMENT_START = re.compile(r"[ \t][#;]")

class ParseError(Exception):
    def __init__(self, message, line):
        super().__init__(f"line {line}: {message}"); self.message = message; self.line = line

def _typed(s):
    low = s.lower()
    if low in BOOLS: return BOOLS[low]
    if low == "null": return None
    if INT.match(s): return int(s)
    if FLOAT.match(s): return float(s)
    return s

def _quoted(s, i, line):
    """s[i] is a quote character. Returns (string, index after the closing quote)."""
    q = s[i]; i += 1
    if q == "'":
        j = s.find("'", i)
        if j < 0: raise ParseError("unterminated string", line)
        return s[i:j], j + 1
    out = []
    while i < len(s):
        c = s[i]
        if c == '"': return "".join(out), i + 1
        if c == "\\":
            if i + 1 >= len(s): raise ParseError("unterminated string", line)
            e = s[i + 1]
            if e not in 'ntr\\"': raise ParseError(f"unknown escape '\\{e}'", line)
            out.append({"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}[e]); i += 2
        else: out.append(c); i += 1
    raise ParseError("unterminated string", line)

def _value(rest, line):
    s = rest.lstrip(" \t")
    if s[:1] in ('"', "'"):
        v, end = _quoted(s, 0, line)
        if not TAIL_OK.match(s[end:]): raise ParseError("unexpected text after closing quote", line)
        return v
    if s[:1] == "[": return _list(s, line)
    m = COMMENT_START.search(rest)
    if m: rest = rest[:m.start()]
    return _typed(rest.strip())

def _list(s, line):
    items, cur, i, n = [], "", 1, len(s)
    parts = []                 # (text, was_quoted)
    quoted = None
    while True:
        if i >= n: raise ParseError("unterminated list", line)
        c = s[i]
        if c in "\"'" and not cur.strip():
            v, i = _quoted(s, i, line); quoted = v; cur = ""
            while i < n and s[i] in " \t": i += 1
            if i < n and s[i] not in ",]": raise ParseError("unexpected text after closing quote", line)
            continue
        if c == "[": raise ParseError("nested list", line)
        if c == ",":
            parts.append((quoted, cur)); cur, quoted = "", None; i += 1; continue
        if c == "]":
            parts.append((quoted, cur)); i += 1; break
        cur += c; i += 1
    if not TAIL_OK.match(s[i:]): raise ParseError("unexpected text after list", line)
    out = []
    for k, (qv, text) in enumerate(parts):
        if qv is not None: out.append(qv); continue
        t = text.strip()
        if not t:
            if len(parts) == 1 or k == len(parts) - 1 and k > 0: continue          # `[]`, or one trailing comma
            raise ParseError("empty list element", line)
        out.append(_typed(t))
    return out

def parse(text):
    if not isinstance(text, str): raise TypeError("text must be a str")
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "": lines.pop()
    result, section, i = {}, "", 0
    while i < len(lines):
        raw, no = lines[i], i + 1; i += 1
        st = raw.strip(" \t")
        if not st or st[0] in "#;": continue
        if st[0] == "[":
            j = st.find("]")
            if j < 0: raise ParseError("unterminated section header", no)
            name = st[1:j].strip(" \t")
            if not NAME.match(name): raise ParseError(f"invalid section name {name!r}", no)
            if not TAIL_OK.match(st[j + 1:]): raise ParseError("unexpected text after section header", no)
            section = name; result.setdefault(section, {}); continue
        logical = raw
        while logical.endswith("\\"):
            if i >= len(lines): raise ParseError("unexpected end of file after line continuation", no)
            logical = logical[:-1] + lines[i].lstrip(" \t"); i += 1
        if "=" not in logical: raise ParseError("missing '='", no)
        key, _, rest = logical.partition("=")
        key = key.strip(" \t")
        if not NAME.match(key): raise ParseError(f"invalid key {key!r}", no)
        sect = result.setdefault(section, {})
        if key in sect: raise ParseError(f"duplicate key '{key}' in section '{section}'", no)
        sect[key] = _value(rest, no)
    return result

def parse_file(path):
    with open(path, encoding="utf-8-sig") as f: return parse(f.read())
