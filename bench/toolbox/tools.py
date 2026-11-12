"""The toolbox benchmark's tools: what the spec says about each, the examples the spec shows and the cases the hidden tests run.

Nothing here holds an expected output. `make.py` runs the real tool of the same name on this machine (LC_ALL=C) for every example and case and
writes what it printed and its exit status into the spec and the hidden tests. A tool's text describes only the options listed; the cases use
only those. All data is ASCII.

A case is (arguments, standard input or None). Arguments naming a file use the fixtures below, which are in the seed and are written again by
every hidden test.
"""

FIXTURES = {
    "fruits.txt": "banana\napple\ncherry\napple\nBanana\ndate\n",
    "nums.txt": "10\n9\n-3\n2.5\n100\n9\n",
    "table.csv": "name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n",
    "left.txt": "apple\nbanana\ncherry\nfig\n",
    "right.txt": "banana\ncherry\ndate\nfig\ngrape\n",
    "words.txt": "the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n",
    "empty.txt": "",
}

INTRO = """# Toolbox: small command-line text tools

Write each tool below as its own Python 3 script in the root of the code directory: the tool's name with .py added, run with python3 as the
examples show. Standard library only, no third-party packages, no network, and no tool may run another program. The code the tools share
(reading the inputs, parsing the options) goes in one helper module, `common.py`, which the tools import.

Rules for every tool unless its own text says otherwise:
- Options come before the other arguments. Options without a value may be combined (`-lc` is `-l -c`); an option's value may be attached or be the next argument (`-n3` or `-n 3`).
- A FILE argument names a file to read. With no FILE the tool reads standard input.
- Output goes to standard output exactly as described, byte for byte. All text is ASCII; a line ends with a newline character.
- The tool exits with status 0 unless its text says otherwise.

The files `fruits.txt`, `nums.txt`, `table.csv`, `left.txt`, `right.txt`, `words.txt` and `empty.txt` in the code directory are test data: leave them as they are.

## Requirements
"""

F, N, T, L, R, W, E = "fruits.txt", "nums.txt", "table.csv", "left.txt", "right.txt", "words.txt", "empty.txt"

TOOLS = [
 dict(name="wc", text=(
    "`wc.py [-l] [-w] [-c] [FILE...]` counts lines (newline characters), words (runs of characters separated by spaces, tabs or newlines) and bytes. "
    "With no option it prints all three, always in the order lines, words, bytes; with options only the counts asked for, in that same order. "
    "Each input gives one line: the counts, then a space and the file name (no name for standard input). With more than one FILE a last line gives the sums with the name `total`. "
    "The counts are right-aligned in a common width and separated by one space. The width is the number of digits of the total number of bytes of all the files; for standard input it is 7. "
    "The exception: when exactly one count is printed and there is at most one FILE, the number is printed with no padding. "
    "A FILE that does not exist is skipped with a message on standard error; the other files and the total are still printed and the exit status is 1."),
    examples=[(["-l", F], None), ([W], None), (["-w"], "a b  c\nd\n"), ([], "one two\nthree\n")],
    cases=[([F], None), (["-l", F], None), (["-w", F], None), (["-c", F], None), (["-lc", F], None), (["-c", "-l", F], None), (["-wl", W], None), ([F, W], None),
           (["-l", F, W, N], None), (["-w", F, "missing.txt", N], None), ([E], None), (["-l", E, F], None), ([], "a b c\n"), (["-l"], "x\ny\nz\n"), (["-c"], "no newline"),
           (["-w"], "  spaced   out\twords\n\n"), (["-lw"], "one\ntwo three\n"), ([], ""), (["-l", "missing.txt"], None), (["-lwc", T], None)]),
 dict(name="cut", text=(
    "`cut.py -f LIST [-d DELIM] [-s] [FILE...]` prints the selected fields of each line; `cut.py -c LIST [FILE...]` prints the selected characters. "
    "Fields are separated by the single character DELIM (a tab when `-d` is not given) and are joined by the same character in the output. "
    "LIST is one or more ranges separated by commas: `N` (the Nth, counting from 1), `N-M`, `N-` (N to the end) or `-M` (1 to M). "
    "What is selected is printed once and in the order of the line, whatever the order of the LIST. "
    "With `-f`, a line that does not contain DELIM is printed unchanged, or not at all with `-s`. Fields or characters beyond the end of a line are simply missing."),
    examples=[(["-d,", "-f1,3", T], None), (["-c", "2-4"], "abcdef\n"), (["-d", ":", "-f", "2-"], "a:b:c\nplain\n")],
    cases=[(["-d,", "-f1", T], None), (["-d,", "-f2,3", T], None), (["-d", ",", "-f", "3,1", T], None), (["-d,", "-f2-", T], None), (["-d,", "-f-2", T], None),
           (["-d,", "-f1,3", "-s", T], None), (["-s", "-d,", "-f", "2", T], None), (["-f2"], "a\tb\tc\nno tabs here\n"), (["-f1,3"], "a\tb\tc\td\n"), (["-c1"], "hello\nworld\n"),
           (["-c", "2-4"], "hello\nhi\n\n"), (["-c3-"], "abcdef\nab\n"), (["-c", "-3"], "abcdef\n"), (["-c1,3,5"], "abcdef\n"), (["-c", "5,1-2"], "abcdef\n"),
           (["-d", " ", "-f", "2,4"], "the quick brown fox\n"), (["-d:", "-f5"], "a:b:c\n"), (["-d:", "-f1-2,2-3"], "a:b:c:d\n"), (["-d,", "-f1", F, T], None), (["-d,", "-f2"], "x,,z\n,\n")]),
 dict(name="uniq", text=(
    "`uniq.py [-c] [-d] [-u] [-i] [FILE]` writes its input with each run of adjacent equal lines reduced to the first line of the run. "
    "`-c` puts the length of the run before each line, right-aligned in 7 columns and followed by one space. `-d` prints only runs of two or more lines (one line each); "
    "`-u` prints only lines that are not repeated. `-i` ignores the difference between upper and lower case when comparing; the first line of a run is the one printed."),
    examples=[([], "a\na\nb\na\n"), (["-c"], "x\nx\nx\ny\n"), (["-d"], "a\na\nb\nc\nc\n")],
    cases=[([F], None), (["-c", F], None), ([], "a\na\na\n"), ([], "a\nb\na\nb\n"), (["-c"], "a\na\nb\nb\nb\na\n"), (["-d"], "a\na\nb\nc\nc\nc\n"), (["-u"], "a\na\nb\nc\nc\nd\n"),
           (["-i"], "Apple\napple\nAPPLE\npear\n"), (["-ci"], "b\nB\nb\na\n"), (["-cd"], "a\na\nb\n"), (["-cu"], "a\na\nb\n"), ([], ""), ([], "only\n"), (["-d"], "a\nb\nc\n"),
           (["-u"], "a\na\n"), ([], "\n\n\na\n\n"), (["-c"], "a \na\n"), (["-i", "-u"], "x\nX\ny\n"), (["-c", N], None), (["-d", N], None)]),
 dict(name="seq", text=(
    "`seq.py [-s SEP] [-w] LAST`, `seq.py [-s SEP] [-w] FIRST LAST` or `seq.py [-s SEP] [-w] FIRST INCREMENT LAST` prints the whole numbers from FIRST (1 when not given) "
    "to LAST in steps of INCREMENT (1 when not given; it may be negative, and then the numbers go down to LAST). Nothing is printed when FIRST is already past LAST. "
    "The numbers are separated by SEP, a newline when `-s` is not given, and the output ends with one newline (when there is any output). "
    "`-w` pads every number with leading zeros to the width of the widest one. The arguments are integers and may be negative."),
    examples=[(["-s", ",", "4"], None), (["-s", " ", "2", "3", "11"], None), (["-w", "-s", ":", "8", "10"], None)],
    cases=[(["5"], None), (["3", "6"], None), (["1", "2", "9"], None), (["10", "-3", "0"], None), (["5", "1"], None), (["1", "-1", "5"], None), (["-s", ",", "5"], None),
           (["-s", ", ", "2", "4"], None), (["-w", "8", "12"], None), (["-w", "98", "101"], None), (["-s", "-", "-w", "1", "5", "21"], None), (["0"], None), (["1"], None),
           (["-2", "2"], None), (["3", "3"], None), (["0", "5", "20"], None), (["5", "-5", "-10"], None), (["-s", "", "3"], None), (["2", "2", "7"], None), (["-s", "x", "1", "1", "1"], None)]),
 dict(name="head", text=(
    "`head.py [-n N] [-c N] [FILE...]` prints the first 10 lines of each input; `-n N` the first N lines; `-c N` the first N bytes instead. "
    "An input shorter than that is printed whole. With more than one FILE, each file's part is preceded by a header line `==> NAME <==`, and every header except the first one printed has an empty line before it. "
    "A FILE that does not exist is skipped with a message on standard error (and no header); the others are still printed and the exit status is 1."),
    examples=[(["-n", "2", F], None), (["-c", "5"], "hello world\n"), (["-n1", L, R], None)],
    cases=[([F], None), (["-n", "3", F], None), (["-n1", F], None), (["-n", "0", F], None), (["-n", "100", F], None), (["-c", "4", F], None), (["-c8", W], None), (["-c", "0", F], None),
           (["-n", "2", F, N], None), ([L, R], None), (["-n", "1", F, "missing.txt", N], None), (["-n", "1", "missing.txt", F], None), (["-n", "2"], "a\nb\nc\n"), ([], "1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n12\n"),
           (["-c", "3"], "ab"), (["-n", "2"], "one\ntwo"), ([E], None), (["-n", "1", E, F], None), (["-c", "2", L, R], None), (["-n", "3"], "\n\n\n\n")]),
 dict(name="tail", text=(
    "`tail.py [-n N] [-c N] [FILE]` prints the last 10 lines of its input; `-n N` the last N lines; `-n +N` everything from line N on (counting from 1); `-c N` the last N bytes instead. "
    "An input shorter than that is printed whole."),
    examples=[(["-n", "2", F], None), (["-n", "+5", F], None), (["-c", "4"], "hello\n")],
    cases=[([F], None), (["-n", "3", F], None), (["-n1", F], None), (["-n", "0", F], None), (["-n", "100", F], None), (["-n", "+1", F], None), (["-n", "+3", F], None), (["-n+6", F], None),
           (["-n", "+99", F], None), (["-c", "5", F], None), (["-c", "0", F], None), (["-c", "500", F], None), (["-n", "2"], "a\nb\nc\n"), ([], "1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n12\n"),
           (["-n", "1"], "one\ntwo"), (["-n", "+2"], "one\ntwo"), ([E], None), (["-c", "3"], "ab"), (["-n", "2"], "\n\n\n"), (["-n", "2", W], None)]),
 dict(name="sort", text=(
    "`sort.py [-r] [-n] [-u] [-f] [FILE...]` prints all the lines of its inputs in order. Without options lines are compared as strings of bytes (so every upper-case letter comes before every lower-case one). "
    "`-f` compares as if lower-case letters were upper case. `-n` compares by the number at the start of the line (an optional `-`, digits, an optional decimal part; a line that does not start with a number counts as 0). "
    "Lines that compare equal under `-f` or `-n` are put in plain byte order. `-r` reverses the whole result. "
    "`-u` prints only the first of the lines that compare equal (under `-n`, lines with the same number are equal)."),
    examples=[([F], None), (["-n", N], None), (["-ru"], "b\na\nc\na\n")],
    cases=[([F], None), (["-r", F], None), (["-u", F], None), (["-f", F], None), (["-n", N], None), (["-nr", N], None), (["-nu", N], None), (["-ru", F], None), ([L, R], None), (["-u", L, R], None),
           ([], "b\nA\na\nB\n"), (["-f"], "b\nA\na\nB\n"), (["-n"], "10\n9\nabc\n-1\n2.5\n\n"), (["-n"], "3 apples\n10 pears\n3 figs\n"), ([], "10\n9\n100\n"), ([], ""), ([], "same\nsame\n"),
           (["-r"], "a\nB\nc\n"), (["-n", "-r"], "1.5\n1.25\n-2\n"), (["-u"], "b\nb\na\nb\n")]),
 dict(name="tr", text=(
    "`tr.py SET1 SET2` copies standard input to standard output, replacing each character of SET1 by the character at the same position in SET2; when SET2 is shorter its last character is used for the rest. "
    "`tr.py -d SET1` deletes the characters of SET1. `tr.py -s SET1` replaces each run of one repeated character of SET1 by a single one. "
    "In a set, `X-Y` stands for all the characters from X to Y, and `\\n`, `\\t` and `\\\\` stand for a newline, a tab and a backslash. It reads only standard input."),
    examples=[(["a-z", "A-Z"], "hello world\n"), (["-d", "aeiou"], "education\n"), (["-s", " "], "a  b   c\n")],
    cases=[(["abc", "xyz"], "aabbcc cab\n"), (["a-z", "A-Z"], "Mixed Case 123\n"), (["A-Z", "a-z"], "Mixed Case 123\n"), (["a-y", "b-z"], "hello\n"), (["elo", "x"], "hello\n"), (["0-9", "#"], "tel 555-0199\n"),
           ([" ", "_"], "a b c\n"), (["\\n", " "], "one\ntwo\nthree\n"), (["\\t", " "], "a\tb\n"), (["-d", "0-9"], "a1b22c333\n"), (["-d", "\\n"], "one\ntwo\n"), (["-d", "a-c"], "abcdabcd\n"),
           (["-s", "a"], "aaa bb aa\n"), (["-s", "a-z"], "aabbccdd  ee\n"), (["-s", "\\n"], "a\n\n\nb\n"), (["ab", "ba"], "abba\n"), (["a-c", "x"], "abcabc\n"), (["-d", " "], " s p a c e \n"),
           (["a", "b"], ""), (["-s", " "], "no_runs\n")]),
 dict(name="grep", text=(
    "`grep.py [-i] [-v] [-c] [-n] [-x] PATTERN [FILE...]` prints the lines that contain PATTERN, which is plain text and not a regular expression. "
    "`-i` ignores the difference between upper and lower case. `-x` selects only lines that are equal to PATTERN as a whole. `-v` selects the lines that would not be selected. "
    "`-n` puts the line number (counting from 1 in each file) and a colon before each line. `-c` prints, instead of the lines, the number of selected lines of each input. "
    "With more than one FILE every output line starts with the file name and a colon (before the line number, if any). "
    "The exit status is 0 when at least one line was selected and 1 when none was."),
    examples=[(["an", F], None), (["-n", "-i", "banana", F], None), (["-c", "x"], "abc\n")],
    cases=[(["an", F], None), (["apple", F], None), (["-i", "banana", F], None), (["-v", "an", F], None), (["-c", "a", F], None), (["-n", "a", F], None), (["-x", "apple", F], None), (["-x", "app", F], None),
           (["-vc", "a", F], None), (["-in", "BANANA", F], None), (["fig", L, R], None), (["-n", "fig", L, R], None), (["-c", "an", L, R, F], None), (["-c", "zzz", L, R], None), (["zzz", F], None),
           (["-v", "a"], "abc\nxyz\n"), (["", ], "one\ntwo\n"), (["-n", "o"], "one\ntwo\nthree\n"), (["-x", ""], "a\n\nb\n"), (["a.c"], "abc\na.c\n"), (["-c", "x"], ""), (["-i", "-x", "DATE", F], None)]),
 dict(name="cat", text=(
    "`cat.py [-n] [FILE...]` copies its inputs to standard output one after the other. `-n` numbers every line of the output, counting on from one file to the next: "
    "the number right-aligned in 6 columns, then a tab, then the line. A FILE that does not exist is skipped with a message on standard error; the others are still printed and the exit status is 1."),
    examples=[([L, R], None), (["-n"], "x\ny\n"), ([], "as it is\n")],
    cases=[([F], None), ([L, R], None), (["-n", F], None), (["-n", L, R], None), ([E], None), ([E, F, E], None), ([F, "missing.txt", L], None), (["-n", "missing.txt", L], None), ([], "stdin line\n"),
           (["-n"], "a\n\nb\n"), ([], "no newline at end"), ([], ""), (["-n"], ""), (["-n", W], None), ([F, F], None)]),
 dict(name="nl", text=(
    "`nl.py [-b a] [FILE...]` numbers the lines of its inputs: the number right-aligned in 6 columns, then a tab, then the line. "
    "Empty lines are not numbered and do not count: an empty line is printed as 7 spaces. With `-b a` empty lines are numbered like any other. The numbering runs on from one file to the next."),
    examples=[([L], None), ([], "a\n\nb\n"), (["-b", "a"], "a\n\nb\n")],
    cases=[([F], None), ([W], None), (["-b", "a", W], None), (["-ba", F], None), ([L, R], None), ([], "one\n"), ([], "\n\n"), (["-b", "a"], "\n\n"), ([], ""), ([], "a\n \nb\n"),
           (["-b", "a", E, L], None), ([N], None)]),
 dict(name="paste", text=(
    "`paste.py [-d DELIM] [-s] FILE...` prints lines made of the corresponding lines of each FILE, joined by a tab or by the single character DELIM. "
    "It goes on until the longest file ends; a file that has run out gives an empty field. With `-s` each FILE instead becomes one output line: all its lines joined by the delimiter."),
    examples=[([L, R], None), (["-d", ",", "-s", L], None), (["-d:", L, L], None)],
    cases=[([L, R], None), ([R, L], None), (["-d", ",", L, R], None), (["-d:", L, R, F], None), (["-s", L], None), (["-s", L, R], None), (["-s", "-d", ",", L, R], None), (["-sd", "+", N], None),
           ([L], None), ([E, L], None), ([L, E], None), (["-s", E], None), (["-d", " ", F, N], None), (["-s", "-d", " ", W], None), ([F, F, F], None)]),
 dict(name="comm", text=(
    "`comm.py [-1] [-2] [-3] FILE1 FILE2` compares two files whose lines are in sorted order and prints three columns: lines only in FILE1, lines only in FILE2 and lines in both. "
    "A line of the second column starts with one tab and a line of the third with two tabs. `-1`, `-2` and `-3` leave a column out, and each column left out also removes one of the tabs "
    "from the columns after it. The output is in sorted order."),
    examples=[([L, R], None), (["-12", L, R], None), (["-3", L, R], None)],
    cases=[([L, R], None), ([R, L], None), (["-1", L, R], None), (["-2", L, R], None), (["-3", L, R], None), (["-12", L, R], None), (["-13", L, R], None), (["-23", L, R], None), (["-123", L, R], None),
           ([L, L], None), ([E, L], None), ([L, E], None), (["-1", "-2", R, L], None), ([E, E], None), (["-3", L, L], None)]),
 dict(name="fold", text=(
    "`fold.py [-w WIDTH] [-s] [FILE...]` breaks every line that is longer than WIDTH characters (80 when `-w` is not given) into pieces of at most WIDTH characters, each on its own line. "
    "With `-s` a piece ends after the last space that fits within WIDTH, when there is one, so words stay whole; the space stays at the end of its piece."),
    examples=[(["-w", "5"], "abcdefghijkl\n"), (["-s", "-w", "10"], "the quick brown fox\n"), (["-w3"], "ab\nabcd\n")],
    cases=[(["-w", "5"], "abcdefghijkl\n"), (["-w", "4"], "abcd\n"), (["-w", "4"], "abcde\n"), (["-w1"], "abc\n"), (["-w", "20", W], None), (["-s", "-w", "20", W], None), (["-sw", "12", W], None),
           (["-s", "-w", "5"], "abcdefghij\n"), (["-s", "-w", "6"], "ab cd ef gh\n"), ([], "short\n"), ([], "x" * 85 + "\n"), (["-s"], "word " * 20 + "\n"), (["-w", "3"], "\n\nabcd\n"),
           (["-w", "10", F], None), (["-s", "-w", "8"], "a  b  c  d  e\n"), ([], "")]),
 dict(name="basename", text=(
    "`basename.py NAME [SUFFIX]` prints NAME without its directory part: trailing slashes are removed first, then everything up to and including the last slash. A NAME made only of slashes gives `/`. "
    "When SUFFIX is given and the result ends with it and is longer than it, the suffix is removed too. It reads no input."),
    examples=[(["/usr/local/lib/"], None), (["src/main.py", ".py"], None), (["notes.txt"], None)],
    cases=[(["/usr/bin/sort"], None), (["dir/file.txt"], None), (["file.txt"], None), (["/usr/lib/"], None), (["/"], None), (["///"], None), (["a/b/c//"], None), (["archive.tar.gz", ".gz"], None),
           (["archive.tar.gz", ".tar"], None), ([".txt", ".txt"], None), (["dir/readme", "me"], None), (["dir/x.py", "x.py"], None), (["a b/c d"], None), ([".hidden"], None), (["dir.d/"], None)]),
 dict(name="dirname", text=(
    "`dirname.py NAME...` prints, for each NAME on its own line, the name without its last component: trailing slashes are removed, then the last component and the slashes before it. "
    "A NAME with no slash gives `.`; a NAME whose only slashes are at its start gives `/`. It reads no input."),
    examples=[(["/usr/local/lib"], None), (["notes.txt"], None), (["/etc/"], None)],
    cases=[(["/usr/bin/sort"], None), (["dir/file.txt"], None), (["file.txt"], None), (["/usr/lib/"], None), (["/"], None), (["///"], None), (["a/b/c//"], None), (["a//b"], None), (["/x"], None),
           (["a/b", "c", "/d/e/"], None), (["."], None), ([".."], None), (["a/"], None), (["//a"], None), (["a b/c d"], None)]),
 dict(name="tac", text=(
    "`tac.py [FILE...]` prints the lines of each input in reverse order, last line first; the files themselves stay in the order given."),
    examples=[([L], None), ([], "1\n2\n3\n"), ([L, R], None)],
    cases=[([F], None), ([L, R], None), ([], "a\nb\nc\n"), ([], "only\n"), ([], ""), ([E, L], None), ([], "\n\nx\n"), ([W], None), ([N, F], None), ([], "a\n\nb\n")]),
 dict(name="rev", text=(
    "`rev.py [FILE...]` prints every line with its characters in reverse order; the lines stay in their order."),
    examples=[([], "hello\n"), ([L], None), ([], "ab cd\nxyz\n")],
    cases=[([F], None), ([L, R], None), ([], "abc\n"), ([], "a\n"), ([], ""), ([], "\n\n"), ([], "a b  c\n"), ([W], None), ([], "12345\n67\n"), ([E, L], None)]),
 dict(name="expand", text=(
    "`expand.py [-t N] [FILE...]` replaces every tab by spaces up to the next tab stop. Tab stops are every N columns, 8 when `-t` is not given: a tab always adds at least one space and ends at a column that is a multiple of N."),
    examples=[(["-t", "4"], "a\tbc\tdef\n"), ([], "x\ty\n"), (["-t2"], "\tab\tc\n")],
    cases=[([], "a\tb\n"), ([], "\tindented\n"), ([], "12345678\tx\n"), ([], "1234567\tx\n"), (["-t", "4"], "a\tbc\tdef\tg\n"), (["-t4"], "abcd\te\n"), (["-t", "1"], "a\tb\tc\n"),
           (["-t", "3"], "\t\tx\n"), ([], "no tabs\n"), ([], ""), ([], "a\tb\nc\td\te\n"), (["-t", "10"], "name\tvalue\n"), ([F], None), (["-t", "2"], "ab\tcd\tef\n")]),
 dict(name="b64", real="base64", text=(
    "`b64.py [-d] [-w COLS] [FILE]` prints its input encoded in base64 (the standard alphabet, with `=` padding), broken into lines of 76 characters; `-w COLS` sets another line length and `-w 0` puts "
    "everything on one line with no newline at the end. Otherwise the output ends with a newline when there is any. `-d` decodes instead: newlines in the input are ignored and the decoded bytes are printed as they are."),
    examples=[([], "hello\n"), (["-d"], "aGVsbG8K\n"), (["-w", "8"], "the quick brown fox\n")],
    cases=[([], "hello\n"), ([], "a"), ([], "ab"), ([], "abc"), ([], ""), ([F], None), ([W], None), (["-w", "20", W], None), (["-w", "0", F], None), (["-w0"], "abc"), (["-w", "4"], "abcdefgh"),
           (["-d"], "aGVsbG8K\n"), (["-d"], "YQ==\n"), (["-d"], "YWJj"), (["-d"], "dGhlIHF1aWNr\nIGJyb3duIGZv\neAo=\n"), (["-d"], "")]),
]

SIZES = {"toolbox_04": 4, "toolbox_10": 10, "toolbox_20": 20}
