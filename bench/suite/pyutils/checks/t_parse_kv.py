import sys
sys.path.insert(0, ".")
try:
    from textutil import parse_kv
except Exception as e:
    print(f"cannot import parse_kv from textutil.py: {e!r}"); sys.exit(1)
cases = [("a=1;b=two;c=3.5", {"a": 1, "b": "two", "c": 3.5}), (" a = 1 ; b=x ", {"a": 1, "b": "x"}), ("a=1;;b=2;", {"a": 1, "b": 2}), ("", {}), ("n=-4", {"n": -4})]
bad = [(i, parse_kv(i), want) for i, want in cases if parse_kv(i) != want]
for i, got, want in bad: print(f"parse_kv({i!r}) returned {got!r}, expected {want!r}")
sys.exit(1 if bad else 0)
