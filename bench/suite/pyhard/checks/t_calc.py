import sys
sys.path.insert(0, ".")
try:
    from calc import evaluate
except Exception as e:
    print(f"cannot import evaluate from calc.py: {e!r}"); sys.exit(1)
good = [("1+2*3", 7), ("(1+2)*3", 9), ("-2+5", 3), ("2*-3", -6), (" 10 / 4 ", 2.5), ("2*(3+4)*5", 70), ("1.5+1.5", 3.0), ("--3", 3), ("8/2/2", 2.0), ("+4", 4), ("((2))", 2), ("2-3-4", -5)]
bad = ["", "1 +", "(1", "1)", "2 $ 3", "1/0", "()", "1 2", "*3"]
fails = []
for e, want in good:
    try: got = evaluate(e)
    except Exception as ex: got = f"raised {ex!r}"
    if got != want: fails.append(f"evaluate({e!r}) -> {got!r}, expected {want!r}")
for e in bad:
    try: got = evaluate(e); fails.append(f"evaluate({e!r}) returned {got!r}, expected ValueError")
    except ValueError: pass
    except Exception as ex: fails.append(f"evaluate({e!r}) raised {ex!r}, expected ValueError")
print("\n".join(fails)); sys.exit(1 if fails else 0)
