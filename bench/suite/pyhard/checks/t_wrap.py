import sys
sys.path.insert(0, ".")
try:
    from textwrap2 import wrap
except Exception as e:
    print(f"cannot import wrap from textwrap2.py: {e!r}"); sys.exit(1)
cases = [(("the quick brown fox", 10), ["the quick", "brown fox"]), (("abcdefghij", 5), ["abcde", "fghij"]), (("a b c", 1), ["a", "b", "c"]),
         (("a\n\nb", 5), ["a", "", "b"]), (("  lots   of   space  ", 8), ["lots of", "space"]), (("hi abcdefgh", 5), ["hi", "abcde", "fgh"]), (("", 5), []),
         (("one two three four", 9), ["one two", "three", "four"]), (("aaaa bbbb", 9), ["aaaa bbbb"]), (("aaaa bbbb", 8), ["aaaa", "bbbb"])]
f = []
for (t, w), want in cases:
    try: got = wrap(t, w)
    except Exception as e: got = f"raised {e!r}"
    if got != want: f.append(f"wrap({t!r}, {w}) -> {got!r}, expected {want!r}")
try: wrap("x", 0); f.append("wrap(.., 0) must raise ValueError")
except ValueError: pass
except Exception as e: f.append(f"wrap(.., 0) raised {e!r}, expected ValueError")
print("\n".join(f)); sys.exit(1 if f else 0)
