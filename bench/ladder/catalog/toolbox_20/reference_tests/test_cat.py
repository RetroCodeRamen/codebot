"""Hidden tests of cat.py. Every expected output and exit status was printed by the real `cat` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestCat(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "cat.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"cat.py {args} with standard input {stdin!r}")
    def test_r10_c01_fruits_txt(self): self.check(['fruits.txt'], None, 'banana\napple\ncherry\napple\nBanana\ndate\n', 0)
    def test_r10_c02_left_txt_right_txt(self): self.check(['left.txt', 'right.txt'], None, 'apple\nbanana\ncherry\nfig\nbanana\ncherry\ndate\nfig\ngrape\n', 0)
    def test_r10_c03_n_fruits_txt(self): self.check(['-n', 'fruits.txt'], None, '     1\tbanana\n     2\tapple\n     3\tcherry\n     4\tapple\n     5\tBanana\n     6\tdate\n', 0)
    def test_r10_c04_n_left_txt_right_txt(self): self.check(['-n', 'left.txt', 'right.txt'], None, '     1\tapple\n     2\tbanana\n     3\tcherry\n     4\tfig\n     5\tbanana\n     6\tcherry\n     7\tdate\n     8\tfig\n     9\tgrape\n', 0)
    def test_r10_c05_empty_txt(self): self.check(['empty.txt'], None, '', 0)
    def test_r10_c06_empty_txt_fruits_txt_empty_txt(self): self.check(['empty.txt', 'fruits.txt', 'empty.txt'], None, 'banana\napple\ncherry\napple\nBanana\ndate\n', 0)
    def test_r10_c07_fruits_txt_missing_txt_left_txt(self): self.check(['fruits.txt', 'missing.txt', 'left.txt'], None, 'banana\napple\ncherry\napple\nBanana\ndate\napple\nbanana\ncherry\nfig\n', 1)
    def test_r10_c08_n_missing_txt_left_txt(self): self.check(['-n', 'missing.txt', 'left.txt'], None, '     1\tapple\n     2\tbanana\n     3\tcherry\n     4\tfig\n', 1)
    def test_r10_c09_no_arguments(self): self.check([], 'stdin line\n', 'stdin line\n', 0)
    def test_r10_c10_n(self): self.check(['-n'], 'a\n\nb\n', '     1\ta\n     2\t\n     3\tb\n', 0)
    def test_r10_c11_no_arguments(self): self.check([], 'no newline at end', 'no newline at end', 0)
    def test_r10_c12_no_arguments(self): self.check([], '', '', 0)
    def test_r10_c13_n(self): self.check(['-n'], '', '', 0)
    def test_r10_c14_n_words_txt(self): self.check(['-n', 'words.txt'], None, '     1\tthe quick brown fox jumps over the lazy dog\n     2\tpack my box with five dozen liquor jugs\n     3\t\n     4\thow vexingly quick daft zebras jump\n', 0)
    def test_r10_c15_fruits_txt_fruits_txt(self): self.check(['fruits.txt', 'fruits.txt'], None, 'banana\napple\ncherry\napple\nBanana\ndate\nbanana\napple\ncherry\napple\nBanana\ndate\n', 0)
