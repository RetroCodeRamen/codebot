"""Hidden tests of cut.py. Every expected output and exit status was printed by the real `cut` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestCut(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "cut.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"cut.py {args} with standard input {stdin!r}")
    def test_r02_c01_d_f1_table_csv(self): self.check(['-d,', '-f1', 'table.csv'], None, 'name\npear\nfig\nplain line\nkiwi\n', 0)
    def test_r02_c02_d_f2_3_table_csv(self): self.check(['-d,', '-f2,3', 'table.csv'], None, 'qty,price\n3,1.20\n12,0.45\nplain line\n7,0.30\n', 0)
    def test_r02_c03_d_f_3_1_table_csv(self): self.check(['-d', ',', '-f', '3,1', 'table.csv'], None, 'name,price\npear,1.20\nfig,0.45\nplain line\nkiwi,0.30\n', 0)
    def test_r02_c04_d_f2_table_csv(self): self.check(['-d,', '-f2-', 'table.csv'], None, 'qty,price\n3,1.20\n12,0.45\nplain line\n7,0.30\n', 0)
    def test_r02_c05_d_f_2_table_csv(self): self.check(['-d,', '-f-2', 'table.csv'], None, 'name,qty\npear,3\nfig,12\nplain line\nkiwi,7\n', 0)
    def test_r02_c06_d_f1_3_s_table_csv(self): self.check(['-d,', '-f1,3', '-s', 'table.csv'], None, 'name,price\npear,1.20\nfig,0.45\nkiwi,0.30\n', 0)
    def test_r02_c07_s_d_f_2_table_csv(self): self.check(['-s', '-d,', '-f', '2', 'table.csv'], None, 'qty\n3\n12\n7\n', 0)
    def test_r02_c08_f2(self): self.check(['-f2'], 'a\tb\tc\nno tabs here\n', 'b\nno tabs here\n', 0)
    def test_r02_c09_f1_3(self): self.check(['-f1,3'], 'a\tb\tc\td\n', 'a\tc\n', 0)
    def test_r02_c10_c1(self): self.check(['-c1'], 'hello\nworld\n', 'h\nw\n', 0)
    def test_r02_c11_c_2_4(self): self.check(['-c', '2-4'], 'hello\nhi\n\n', 'ell\ni\n\n', 0)
    def test_r02_c12_c3(self): self.check(['-c3-'], 'abcdef\nab\n', 'cdef\n\n', 0)
    def test_r02_c13_c_3(self): self.check(['-c', '-3'], 'abcdef\n', 'abc\n', 0)
    def test_r02_c14_c1_3_5(self): self.check(['-c1,3,5'], 'abcdef\n', 'ace\n', 0)
    def test_r02_c15_c_5_1_2(self): self.check(['-c', '5,1-2'], 'abcdef\n', 'abe\n', 0)
    def test_r02_c16_d_f_2_4(self): self.check(['-d', ' ', '-f', '2,4'], 'the quick brown fox\n', 'quick fox\n', 0)
    def test_r02_c17_d_f5(self): self.check(['-d:', '-f5'], 'a:b:c\n', '\n', 0)
    def test_r02_c18_d_f1_2_2_3(self): self.check(['-d:', '-f1-2,2-3'], 'a:b:c:d\n', 'a:b:c\n', 0)
    def test_r02_c19_d_f1_fruits_txt_table_csv(self): self.check(['-d,', '-f1', 'fruits.txt', 'table.csv'], None, 'banana\napple\ncherry\napple\nBanana\ndate\nname\npear\nfig\nplain line\nkiwi\n', 0)
    def test_r02_c20_d_f2(self): self.check(['-d,', '-f2'], 'x,,z\n,\n', '\n\n', 0)
