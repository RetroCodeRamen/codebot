"""Hidden tests of sort.py. Every expected output and exit status was printed by the real `sort` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestSort(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "sort.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"sort.py {args} with standard input {stdin!r}")
    def test_r07_c01_fruits_txt(self): self.check(['fruits.txt'], None, 'Banana\napple\napple\nbanana\ncherry\ndate\n', 0)
    def test_r07_c02_r_fruits_txt(self): self.check(['-r', 'fruits.txt'], None, 'date\ncherry\nbanana\napple\napple\nBanana\n', 0)
    def test_r07_c03_u_fruits_txt(self): self.check(['-u', 'fruits.txt'], None, 'Banana\napple\nbanana\ncherry\ndate\n', 0)
    def test_r07_c04_f_fruits_txt(self): self.check(['-f', 'fruits.txt'], None, 'apple\napple\nBanana\nbanana\ncherry\ndate\n', 0)
    def test_r07_c05_n_nums_txt(self): self.check(['-n', 'nums.txt'], None, '-3\n2.5\n9\n9\n10\n100\n', 0)
    def test_r07_c06_nr_nums_txt(self): self.check(['-nr', 'nums.txt'], None, '100\n10\n9\n9\n2.5\n-3\n', 0)
    def test_r07_c07_nu_nums_txt(self): self.check(['-nu', 'nums.txt'], None, '-3\n2.5\n9\n10\n100\n', 0)
    def test_r07_c08_ru_fruits_txt(self): self.check(['-ru', 'fruits.txt'], None, 'date\ncherry\nbanana\napple\nBanana\n', 0)
    def test_r07_c09_left_txt_right_txt(self): self.check(['left.txt', 'right.txt'], None, 'apple\nbanana\nbanana\ncherry\ncherry\ndate\nfig\nfig\ngrape\n', 0)
    def test_r07_c10_u_left_txt_right_txt(self): self.check(['-u', 'left.txt', 'right.txt'], None, 'apple\nbanana\ncherry\ndate\nfig\ngrape\n', 0)
    def test_r07_c11_no_arguments(self): self.check([], 'b\nA\na\nB\n', 'A\nB\na\nb\n', 0)
    def test_r07_c12_f(self): self.check(['-f'], 'b\nA\na\nB\n', 'A\na\nB\nb\n', 0)
    def test_r07_c13_n(self): self.check(['-n'], '10\n9\nabc\n-1\n2.5\n\n', '-1\n\nabc\n2.5\n9\n10\n', 0)
    def test_r07_c14_n(self): self.check(['-n'], '3 apples\n10 pears\n3 figs\n', '3 apples\n3 figs\n10 pears\n', 0)
    def test_r07_c15_no_arguments(self): self.check([], '10\n9\n100\n', '10\n100\n9\n', 0)
    def test_r07_c16_no_arguments(self): self.check([], '', '', 0)
    def test_r07_c17_no_arguments(self): self.check([], 'same\nsame\n', 'same\nsame\n', 0)
    def test_r07_c18_r(self): self.check(['-r'], 'a\nB\nc\n', 'c\na\nB\n', 0)
    def test_r07_c19_n_r(self): self.check(['-n', '-r'], '1.5\n1.25\n-2\n', '1.5\n1.25\n-2\n', 0)
    def test_r07_c20_u(self): self.check(['-u'], 'b\nb\na\nb\n', 'a\nb\n', 0)
