# Toolbox: clean-room copies of command-line tools

A benchmark whose expected outcome is known independently of codebot and of whoever wrote the benchmark: the tools are specified in prose
(`tools.py`), and every expected output and exit status, in the spec's examples and in the hidden tests alike, is what the real tool of that
name printed on this machine (`make.py` runs it with `LC_ALL=C`). GNU coreutils 9.4, GNU grep 3.11 (as `grep -F`), util-linux `rev`.

```
python3 bench/toolbox/make.py      # (re)writes bench/ladder/catalog/toolbox_04, _10, _20 and validates them
```

| entry | tools | spec examples | hidden tests |
|---|---|---|---|
| `toolbox_04` | wc, cut, uniq, seq | 13 | 80 |
| `toolbox_10` | + head, tail, sort, tr, grep, cat | 31 | 197 |
| `toolbox_20` | + nl, paste, comm, fold, basename, dirname, tac, rev, expand, b64 (base64) | 61 | 335 |

The size is the point. It is the same kind of work at 4, 10 and 20 tools, so it shows where one agent's context window stops being enough: by
the Team Designer's rule (`teamgen.fit`) 4 and 10 tools fit one context at NUM_CTX 16384 and 20 do not. Each tool is its own script, they share
one helper module (`common.py`), and each is small enough for one reply.

What is and is not independent:
- The expected values are the real tools'. Nobody wrote them.
- The prose describing each tool, the choice of options (a pinned subset of each tool) and the choice of test inputs are this project's. A
  wrong description would cost both arms of a comparison the same. `reference/` is a separate implementation written from the prose alone; it
  passes all 335 hidden tests and every spec example, and the tests pass 0 on the empty seed, which shows the prose matches the real tools for
  the cases tested.
- The model has seen these tools' source in training, so scores flatter every arm; comparisons between arms are what the benchmark is for.
- Only standard output and the exit status are compared, never standard error. All data is ASCII.
