"""Hidden tests of fold.py. Every expected output and exit status was printed by the real `fold` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestFold(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "fold.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"fold.py {args} with standard input {stdin!r}")
    def test_r14_c01_w_5(self): self.check(['-w', '5'], 'abcdefghijkl\n', 'abcde\nfghij\nkl\n', 0)
    def test_r14_c02_w_4(self): self.check(['-w', '4'], 'abcd\n', 'abcd\n', 0)
    def test_r14_c03_w_4(self): self.check(['-w', '4'], 'abcde\n', 'abcd\ne\n', 0)
    def test_r14_c04_w1(self): self.check(['-w1'], 'abc\n', 'a\nb\nc\n', 0)
    def test_r14_c05_w_20_words_txt(self): self.check(['-w', '20', 'words.txt'], None, 'the quick brown fox \njumps over the lazy \ndog\npack my box with fiv\ne dozen liquor jugs\n\nhow vexingly quick d\naft zebras jump\n', 0)
    def test_r14_c06_s_w_20_words_txt(self): self.check(['-s', '-w', '20', 'words.txt'], None, 'the quick brown fox \njumps over the lazy \ndog\npack my box with \nfive dozen liquor \njugs\n\nhow vexingly quick \ndaft zebras jump\n', 0)
    def test_r14_c07_sw_12_words_txt(self): self.check(['-sw', '12', 'words.txt'], None, 'the quick \nbrown fox \njumps over \nthe lazy dog\npack my box \nwith five \ndozen \nliquor jugs\n\nhow \nvexingly \nquick daft \nzebras jump\n', 0)
    def test_r14_c08_s_w_5(self): self.check(['-s', '-w', '5'], 'abcdefghij\n', 'abcde\nfghij\n', 0)
    def test_r14_c09_s_w_6(self): self.check(['-s', '-w', '6'], 'ab cd ef gh\n', 'ab cd \nef gh\n', 0)
    def test_r14_c10_no_arguments(self): self.check([], 'short\n', 'short\n', 0)
    def test_r14_c11_no_arguments(self): self.check([], 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n', 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\nxxxxx\n', 0)
    def test_r14_c12_s(self): self.check(['-s'], 'word word word word word word word word word word word word word word word word word word word word \n', 'word word word word word word word word word word word word word word word word \nword word word word \n', 0)
    def test_r14_c13_w_3(self): self.check(['-w', '3'], '\n\nabcd\n', '\n\nabc\nd\n', 0)
    def test_r14_c14_w_10_fruits_txt(self): self.check(['-w', '10', 'fruits.txt'], None, 'banana\napple\ncherry\napple\nBanana\ndate\n', 0)
    def test_r14_c15_s_w_8(self): self.check(['-s', '-w', '8'], 'a  b  c  d  e\n', 'a  b  c \n d  e\n', 0)
    def test_r14_c16_no_arguments(self): self.check([], '', '', 0)
