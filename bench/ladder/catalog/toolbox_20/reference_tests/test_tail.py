"""Hidden tests of tail.py. Every expected output and exit status was printed by the real `tail` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestTail(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "tail.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"tail.py {args} with standard input {stdin!r}")
    def test_r06_c01_fruits_txt(self): self.check(['fruits.txt'], None, 'banana\napple\ncherry\napple\nBanana\ndate\n', 0)
    def test_r06_c02_n_3_fruits_txt(self): self.check(['-n', '3', 'fruits.txt'], None, 'apple\nBanana\ndate\n', 0)
    def test_r06_c03_n1_fruits_txt(self): self.check(['-n1', 'fruits.txt'], None, 'date\n', 0)
    def test_r06_c04_n_0_fruits_txt(self): self.check(['-n', '0', 'fruits.txt'], None, '', 0)
    def test_r06_c05_n_100_fruits_txt(self): self.check(['-n', '100', 'fruits.txt'], None, 'banana\napple\ncherry\napple\nBanana\ndate\n', 0)
    def test_r06_c06_n_1_fruits_txt(self): self.check(['-n', '+1', 'fruits.txt'], None, 'banana\napple\ncherry\napple\nBanana\ndate\n', 0)
    def test_r06_c07_n_3_fruits_txt(self): self.check(['-n', '+3', 'fruits.txt'], None, 'cherry\napple\nBanana\ndate\n', 0)
    def test_r06_c08_n_6_fruits_txt(self): self.check(['-n+6', 'fruits.txt'], None, 'date\n', 0)
    def test_r06_c09_n_99_fruits_txt(self): self.check(['-n', '+99', 'fruits.txt'], None, '', 0)
    def test_r06_c10_c_5_fruits_txt(self): self.check(['-c', '5', 'fruits.txt'], None, 'date\n', 0)
    def test_r06_c11_c_0_fruits_txt(self): self.check(['-c', '0', 'fruits.txt'], None, '', 0)
    def test_r06_c12_c_500_fruits_txt(self): self.check(['-c', '500', 'fruits.txt'], None, 'banana\napple\ncherry\napple\nBanana\ndate\n', 0)
    def test_r06_c13_n_2(self): self.check(['-n', '2'], 'a\nb\nc\n', 'b\nc\n', 0)
    def test_r06_c14_no_arguments(self): self.check([], '1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n12\n', '3\n4\n5\n6\n7\n8\n9\n10\n11\n12\n', 0)
    def test_r06_c15_n_1(self): self.check(['-n', '1'], 'one\ntwo', 'two', 0)
    def test_r06_c16_n_2(self): self.check(['-n', '+2'], 'one\ntwo', 'two', 0)
    def test_r06_c17_empty_txt(self): self.check(['empty.txt'], None, '', 0)
    def test_r06_c18_c_3(self): self.check(['-c', '3'], 'ab', 'ab', 0)
    def test_r06_c19_n_2(self): self.check(['-n', '2'], '\n\n\n', '\n\n', 0)
    def test_r06_c20_n_2_words_txt(self): self.check(['-n', '2', 'words.txt'], None, '\nhow vexingly quick daft zebras jump\n', 0)
