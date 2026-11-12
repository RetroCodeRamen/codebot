"""Hidden tests of wc.py. Every expected output and exit status was printed by the real `wc` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestWc(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "wc.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"wc.py {args} with standard input {stdin!r}")
    def test_r01_c01_fruits_txt(self): self.check(['fruits.txt'], None, ' 6  6 38 fruits.txt\n', 0)
    def test_r01_c02_l_fruits_txt(self): self.check(['-l', 'fruits.txt'], None, '6 fruits.txt\n', 0)
    def test_r01_c03_w_fruits_txt(self): self.check(['-w', 'fruits.txt'], None, '6 fruits.txt\n', 0)
    def test_r01_c04_c_fruits_txt(self): self.check(['-c', 'fruits.txt'], None, '38 fruits.txt\n', 0)
    def test_r01_c05_lc_fruits_txt(self): self.check(['-lc', 'fruits.txt'], None, ' 6 38 fruits.txt\n', 0)
    def test_r01_c06_c_l_fruits_txt(self): self.check(['-c', '-l', 'fruits.txt'], None, ' 6 38 fruits.txt\n', 0)
    def test_r01_c07_wl_words_txt(self): self.check(['-wl', 'words.txt'], None, '  4  23 words.txt\n', 0)
    def test_r01_c08_fruits_txt_words_txt(self): self.check(['fruits.txt', 'words.txt'], None, '  6   6  38 fruits.txt\n  4  23 121 words.txt\n 10  29 159 total\n', 0)
    def test_r01_c09_l_fruits_txt_words_txt_nums_txt(self): self.check(['-l', 'fruits.txt', 'words.txt', 'nums.txt'], None, '  6 fruits.txt\n  4 words.txt\n  6 nums.txt\n 16 total\n', 0)
    def test_r01_c10_w_fruits_txt_missing_txt_nums_txt(self): self.check(['-w', 'fruits.txt', 'missing.txt', 'nums.txt'], None, ' 6 fruits.txt\n 6 nums.txt\n12 total\n', 1)
    def test_r01_c11_empty_txt(self): self.check(['empty.txt'], None, '0 0 0 empty.txt\n', 0)
    def test_r01_c12_l_empty_txt_fruits_txt(self): self.check(['-l', 'empty.txt', 'fruits.txt'], None, ' 0 empty.txt\n 6 fruits.txt\n 6 total\n', 0)
    def test_r01_c13_no_arguments(self): self.check([], 'a b c\n', '      1       3       6\n', 0)
    def test_r01_c14_l(self): self.check(['-l'], 'x\ny\nz\n', '3\n', 0)
    def test_r01_c15_c(self): self.check(['-c'], 'no newline', '10\n', 0)
    def test_r01_c16_w(self): self.check(['-w'], '  spaced   out\twords\n\n', '3\n', 0)
    def test_r01_c17_lw(self): self.check(['-lw'], 'one\ntwo three\n', '      2       3\n', 0)
    def test_r01_c18_no_arguments(self): self.check([], '', '      0       0       0\n', 0)
    def test_r01_c19_l_missing_txt(self): self.check(['-l', 'missing.txt'], None, '', 1)
    def test_r01_c20_lwc_table_csv(self): self.check(['-lwc', 'table.csv'], None, ' 5  6 62 table.csv\n', 0)
