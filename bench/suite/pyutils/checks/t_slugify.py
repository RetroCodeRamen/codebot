import sys
sys.path.insert(0, ".")
try:
    from textutil import slugify
except Exception as e:
    print(f"cannot import slugify from textutil.py: {e!r}"); sys.exit(1)
cases = [("Hello World", "hello-world"), ("  Foo,  Bar!! ", "foo-bar"), ("a--b", "a-b"), ("", ""), ("Already-Slug", "already-slug"), ("x_y z", "x-y-z"), ("--A--", "a")]
bad = [(i, slugify(i), want) for i, want in cases if slugify(i) != want]
for i, got, want in bad: print(f"slugify({i!r}) returned {got!r}, expected {want!r}")
sys.exit(1 if bad else 0)
