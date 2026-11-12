"""Hidden tests of uniq.py. Every expected output and exit status was printed by the real `uniq` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestUniq(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "uniq.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"uniq.py {args} with standard input {stdin!r}")
    def test_r03_c01_fruits_txt(self): self.check(['fruits.txt'], None, 'banana\napple\ncherry\napple\nBanana\ndate\n', 0)
    def test_r03_c02_c_fruits_txt(self): self.check(['-c', 'fruits.txt'], None, '      1 banana\n      1 apple\n      1 cherry\n      1 apple\n      1 Banana\n      1 date\n', 0)
    def test_r03_c03_no_arguments(self): self.check([], 'a\na\na\n', 'a\n', 0)
    def test_r03_c04_no_arguments(self): self.check([], 'a\nb\na\nb\n', 'a\nb\na\nb\n', 0)
    def test_r03_c05_c(self): self.check(['-c'], 'a\na\nb\nb\nb\na\n', '      2 a\n      3 b\n      1 a\n', 0)
    def test_r03_c06_d(self): self.check(['-d'], 'a\na\nb\nc\nc\nc\n', 'a\nc\n', 0)
    def test_r03_c07_u(self): self.check(['-u'], 'a\na\nb\nc\nc\nd\n', 'b\nd\n', 0)
    def test_r03_c08_i(self): self.check(['-i'], 'Apple\napple\nAPPLE\npear\n', 'Apple\npear\n', 0)
    def test_r03_c09_ci(self): self.check(['-ci'], 'b\nB\nb\na\n', '      3 b\n      1 a\n', 0)
    def test_r03_c10_cd(self): self.check(['-cd'], 'a\na\nb\n', '      2 a\n', 0)
    def test_r03_c11_cu(self): self.check(['-cu'], 'a\na\nb\n', '      1 b\n', 0)
    def test_r03_c12_no_arguments(self): self.check([], '', '', 0)
    def test_r03_c13_no_arguments(self): self.check([], 'only\n', 'only\n', 0)
    def test_r03_c14_d(self): self.check(['-d'], 'a\nb\nc\n', '', 0)
    def test_r03_c15_u(self): self.check(['-u'], 'a\na\n', '', 0)
    def test_r03_c16_no_arguments(self): self.check([], '\n\n\na\n\n', '\na\n\n', 0)
    def test_r03_c17_c(self): self.check(['-c'], 'a \na\n', '      1 a \n      1 a\n', 0)
    def test_r03_c18_i_u(self): self.check(['-i', '-u'], 'x\nX\ny\n', 'y\n', 0)
    def test_r03_c19_c_nums_txt(self): self.check(['-c', 'nums.txt'], None, '      1 10\n      1 9\n      1 -3\n      1 2.5\n      1 100\n      1 9\n', 0)
    def test_r03_c20_d_nums_txt(self): self.check(['-d', 'nums.txt'], None, '', 0)
