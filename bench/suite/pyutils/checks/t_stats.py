import sys
sys.path.insert(0, ".")
bad = []
try:
    from stats import mean
    for xs, want in (([], 0.0), ([1, 2, 3], 2.0), ([5], 5.0)):
        try: got = mean(xs)
        except Exception as e: got = repr(e)
        if got != want: bad.append(f"mean({xs}) returned {got!r}, expected {want!r}")
except Exception as e: bad.append(f"cannot import mean: {e!r}")
try:
    from stats import median
    for xs, want in (([], 0.0), ([3, 1, 2], 2), ([4, 1, 3, 2], 2.5), ([7], 7)):
        got = median(xs)
        if got != want: bad.append(f"median({xs}) returned {got!r}, expected {want!r}")
except Exception as e: bad.append(f"stats.py has no working median: {e!r}")
print("\n".join(bad)); sys.exit(1 if bad else 0)
