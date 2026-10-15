# Utility library

Build a small Python utility library as plain modules in the root of the code directory. Python 3, standard library only, no third-party packages, no network.
Every function below must behave exactly as described. Invalid input that the text does not define must not be silently accepted: where an exception is named, raise exactly that class.

## Requirements

1. `textutil.py` provides `slugify(text)`. It lowercases the text and turns every run of one or more characters that are not ASCII letters or digits (`a-z`, `A-Z`, `0-9`) into a single hyphen, then removes any leading and trailing hyphen. Non-ASCII letters count as non-alphanumeric. `slugify("Hello World")` is `"hello-world"`, `slugify("  Foo,  Bar!! ")` is `"foo-bar"`, `slugify("a--b")` is `"a-b"`, `slugify("Café au lait")` is `"caf-au-lait"`, `slugify("")` is `""`, `slugify("---")` is `""`. A value that is not a `str` raises `TypeError`.

2. `textutil.py` also provides `truncate(text, width, suffix="...")`. If `len(text) <= width` the text is returned unchanged. Otherwise the result has exactly `width` characters: the start of the text followed by the suffix, so `truncate("hello world", 8)` is `"hello..."`. If `width` is smaller than the suffix, the result is the first `width` characters of the suffix (`truncate("hello world", 2)` is `".."`, `truncate("hello", 0)` is `""`). A negative `width` raises `ValueError`.

3. `numutil.py` provides `clamp(value, low, high)`. It returns `low` if `value < low`, `high` if `value > high`, otherwise `value` itself, and works for ints and floats (`clamp(5, 1, 3)` is `3`, `clamp(-1, 0, 10)` is `0`, `clamp(2.5, 0, 10)` is `2.5`). If `low > high` it raises `ValueError`; `low == high` is allowed.

4. `numutil.py` also provides `is_prime(n)`. It returns `True` exactly when `n` is a prime number: `is_prime(2)` and `is_prime(97)` are `True`; `is_prime(1)`, `is_prime(0)`, `is_prime(-7)` and `is_prime(561)` are `False`. It must be fast enough for `is_prime(1_000_000_007)` (`True`) to finish well under a second. A value that is not an `int`, including `bool`, raises `TypeError`.

5. `fileutil.py` provides `safe_filename(name)`. Every character that is not one of `A-Z`, `a-z`, `0-9`, `.`, `_`, `-` is replaced by one underscore (each character separately, so `"a  b"` becomes `"a__b"`), then all leading dots are removed, then the result is cut to at most 255 characters. If nothing is left the result is `"unnamed"`. `safe_filename("report 2024.pdf")` is `"report_2024.pdf"`, `safe_filename("../etc/passwd")` is `"_etc_passwd"`, `safe_filename("naïve.txt")` is `"na_ve.txt"`, `safe_filename("...")` is `"unnamed"`, `safe_filename("")` is `"unnamed"`.

6. `fileutil.py` also provides `split_ext(name)`, which returns a tuple `(stem, ext)` where `ext` includes its leading dot. Only the last dot starts the extension, a dot at the very start of the name never does, and a name ending in a dot has no extension: `split_ext("archive.tar.gz")` is `("archive.tar", ".gz")`, `split_ext("README")` is `("README", "")`, `split_ext(".bashrc")` is `(".bashrc", "")`, `split_ext("a.")` is `("a.", "")`, `split_ext("photo.JPG")` is `("photo", ".JPG")`. A name that contains `/` raises `ValueError`.

7. `netutil.py` provides `is_valid_ipv4(text)`. It returns `True` only for a string made of exactly four decimal octets separated by single dots, each octet written with ASCII digits only, in the range 0 to 255, with no leading zeros (the octet `0` itself is fine) and no sign, spaces or other characters anywhere (a trailing newline makes it invalid). `"192.168.1.1"`, `"0.0.0.0"` and `"255.255.255.255"` are valid; `"256.1.1.1"`, `"1.2.3"`, `"1.2.3.4.5"`, `"01.2.3.4"`, `"1.2.3.4 "`, `"-1.2.3.4"` and `""` are not. A value that is not a `str` returns `False` (it never raises).

8. `netutil.py` also provides `ipv4_to_int(text)`, which converts a valid IPv4 address to its 32-bit integer value with the first octet as the most significant byte: `ipv4_to_int("0.0.0.1")` is `1`, `ipv4_to_int("1.0.0.0")` is `16777216`, `ipv4_to_int("192.168.0.1")` is `3232235521`, `ipv4_to_int("255.255.255.255")` is `4294967295`. An address that `is_valid_ipv4` rejects raises `ValueError`.
