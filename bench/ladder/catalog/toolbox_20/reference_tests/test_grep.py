"""Hidden tests of grep.py. Every expected output and exit status was printed by the real `grep` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestGrep(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "grep.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"grep.py {args} with standard input {stdin!r}")
    def test_r09_c01_an_fruits_txt(self): self.check(['an', 'fruits.txt'], None, 'banana\nBanana\n', 0)
    def test_r09_c02_apple_fruits_txt(self): self.check(['apple', 'fruits.txt'], None, 'apple\napple\n', 0)
    def test_r09_c03_i_banana_fruits_txt(self): self.check(['-i', 'banana', 'fruits.txt'], None, 'banana\nBanana\n', 0)
    def test_r09_c04_v_an_fruits_txt(self): self.check(['-v', 'an', 'fruits.txt'], None, 'apple\ncherry\napple\ndate\n', 0)
    def test_r09_c05_c_a_fruits_txt(self): self.check(['-c', 'a', 'fruits.txt'], None, '5\n', 0)
    def test_r09_c06_n_a_fruits_txt(self): self.check(['-n', 'a', 'fruits.txt'], None, '1:banana\n2:apple\n4:apple\n5:Banana\n6:date\n', 0)
    def test_r09_c07_x_apple_fruits_txt(self): self.check(['-x', 'apple', 'fruits.txt'], None, 'apple\napple\n', 0)
    def test_r09_c08_x_app_fruits_txt(self): self.check(['-x', 'app', 'fruits.txt'], None, '', 1)
    def test_r09_c09_vc_a_fruits_txt(self): self.check(['-vc', 'a', 'fruits.txt'], None, '1\n', 0)
    def test_r09_c10_in_banana_fruits_txt(self): self.check(['-in', 'BANANA', 'fruits.txt'], None, '1:banana\n5:Banana\n', 0)
    def test_r09_c11_fig_left_txt_right_txt(self): self.check(['fig', 'left.txt', 'right.txt'], None, 'left.txt:fig\nright.txt:fig\n', 0)
    def test_r09_c12_n_fig_left_txt_right_txt(self): self.check(['-n', 'fig', 'left.txt', 'right.txt'], None, 'left.txt:4:fig\nright.txt:4:fig\n', 0)
    def test_r09_c13_c_an_left_txt_right_txt_fruits_txt(self): self.check(['-c', 'an', 'left.txt', 'right.txt', 'fruits.txt'], None, 'left.txt:1\nright.txt:1\nfruits.txt:2\n', 0)
    def test_r09_c14_c_zzz_left_txt_right_txt(self): self.check(['-c', 'zzz', 'left.txt', 'right.txt'], None, 'left.txt:0\nright.txt:0\n', 1)
    def test_r09_c15_zzz_fruits_txt(self): self.check(['zzz', 'fruits.txt'], None, '', 1)
    def test_r09_c16_v_a(self): self.check(['-v', 'a'], 'abc\nxyz\n', 'xyz\n', 0)
    def test_r09_c17_no_arguments(self): self.check([''], 'one\ntwo\n', 'one\ntwo\n', 0)
    def test_r09_c18_n_o(self): self.check(['-n', 'o'], 'one\ntwo\nthree\n', '1:one\n2:two\n', 0)
    def test_r09_c19_x(self): self.check(['-x', ''], 'a\n\nb\n', '\n', 0)
    def test_r09_c20_a_c(self): self.check(['a.c'], 'abc\na.c\n', 'a.c\n', 0)
    def test_r09_c21_c_x(self): self.check(['-c', 'x'], '', '0\n', 1)
    def test_r09_c22_i_x_date_fruits_txt(self): self.check(['-i', '-x', 'DATE', 'fruits.txt'], None, 'date\n', 0)
