A small Python utility project. The code directory already contains stats.py (with a bug), legacy.py and main_app.py. Build these five things, in this order:

1. textutil.py with slugify(text): lowercase; every run of non-alphanumeric characters becomes a single hyphen; no leading or trailing hyphen. slugify("Hello World") == "hello-world", slugify("  Foo,  Bar!! ") == "foo-bar", slugify("a--b") == "a-b", slugify("") == "".
2. textutil.py with parse_kv(line): parse "a=1;b=two;c=3.5" into {"a": 1, "b": "two", "c": 3.5} (ints stay int, decimals become float, everything else stays a string; empty items and surrounding spaces are ignored).
3. stats.py: mean([]) must return 0.0 instead of crashing (mean([1,2,3]) is 2.0); also add median(xs) (odd and even length; empty list returns 0.0).
4. cli.py (argparse): `python3 cli.py slug "Hello World"` prints hello-world; `python3 cli.py kv "a=1;b=2"` prints the JSON {"a": 1, "b": 2}. It uses the functions from textutil.py.
5. Rename the function old_name to double everywhere (legacy.py and main_app.py); `python3 main_app.py` must still print 42.

Standard library only, Python 3. Keep each change small and keep the existing style.
