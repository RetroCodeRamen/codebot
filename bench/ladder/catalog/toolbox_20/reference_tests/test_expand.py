"""Hidden tests of expand.py. Every expected output and exit status was printed by the real `expand` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestExpand(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "expand.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"expand.py {args} with standard input {stdin!r}")
    def test_r19_c01_no_arguments(self): self.check([], 'a\tb\n', 'a       b\n', 0)
    def test_r19_c02_no_arguments(self): self.check([], '\tindented\n', '        indented\n', 0)
    def test_r19_c03_no_arguments(self): self.check([], '12345678\tx\n', '12345678        x\n', 0)
    def test_r19_c04_no_arguments(self): self.check([], '1234567\tx\n', '1234567 x\n', 0)
    def test_r19_c05_t_4(self): self.check(['-t', '4'], 'a\tbc\tdef\tg\n', 'a   bc  def g\n', 0)
    def test_r19_c06_t4(self): self.check(['-t4'], 'abcd\te\n', 'abcd    e\n', 0)
    def test_r19_c07_t_1(self): self.check(['-t', '1'], 'a\tb\tc\n', 'a b c\n', 0)
    def test_r19_c08_t_3(self): self.check(['-t', '3'], '\t\tx\n', '      x\n', 0)
    def test_r19_c09_no_arguments(self): self.check([], 'no tabs\n', 'no tabs\n', 0)
    def test_r19_c10_no_arguments(self): self.check([], '', '', 0)
    def test_r19_c11_no_arguments(self): self.check([], 'a\tb\nc\td\te\n', 'a       b\nc       d       e\n', 0)
    def test_r19_c12_t_10(self): self.check(['-t', '10'], 'name\tvalue\n', 'name      value\n', 0)
    def test_r19_c13_fruits_txt(self): self.check(['fruits.txt'], None, 'banana\napple\ncherry\napple\nBanana\ndate\n', 0)
    def test_r19_c14_t_2(self): self.check(['-t', '2'], 'ab\tcd\tef\n', 'ab  cd  ef\n', 0)
