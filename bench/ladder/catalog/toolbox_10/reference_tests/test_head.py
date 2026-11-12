"""Hidden tests of head.py. Every expected output and exit status was printed by the real `head` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestHead(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "head.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"head.py {args} with standard input {stdin!r}")
    def test_r05_c01_fruits_txt(self): self.check(['fruits.txt'], None, 'banana\napple\ncherry\napple\nBanana\ndate\n', 0)
    def test_r05_c02_n_3_fruits_txt(self): self.check(['-n', '3', 'fruits.txt'], None, 'banana\napple\ncherry\n', 0)
    def test_r05_c03_n1_fruits_txt(self): self.check(['-n1', 'fruits.txt'], None, 'banana\n', 0)
    def test_r05_c04_n_0_fruits_txt(self): self.check(['-n', '0', 'fruits.txt'], None, '', 0)
    def test_r05_c05_n_100_fruits_txt(self): self.check(['-n', '100', 'fruits.txt'], None, 'banana\napple\ncherry\napple\nBanana\ndate\n', 0)
    def test_r05_c06_c_4_fruits_txt(self): self.check(['-c', '4', 'fruits.txt'], None, 'bana', 0)
    def test_r05_c07_c8_words_txt(self): self.check(['-c8', 'words.txt'], None, 'the quic', 0)
    def test_r05_c08_c_0_fruits_txt(self): self.check(['-c', '0', 'fruits.txt'], None, '', 0)
    def test_r05_c09_n_2_fruits_txt_nums_txt(self): self.check(['-n', '2', 'fruits.txt', 'nums.txt'], None, '==> fruits.txt <==\nbanana\napple\n\n==> nums.txt <==\n10\n9\n', 0)
    def test_r05_c10_left_txt_right_txt(self): self.check(['left.txt', 'right.txt'], None, '==> left.txt <==\napple\nbanana\ncherry\nfig\n\n==> right.txt <==\nbanana\ncherry\ndate\nfig\ngrape\n', 0)
    def test_r05_c11_n_1_fruits_txt_missing_txt_nums_txt(self): self.check(['-n', '1', 'fruits.txt', 'missing.txt', 'nums.txt'], None, '==> fruits.txt <==\nbanana\n\n==> nums.txt <==\n10\n', 1)
    def test_r05_c12_n_1_missing_txt_fruits_txt(self): self.check(['-n', '1', 'missing.txt', 'fruits.txt'], None, '==> fruits.txt <==\nbanana\n', 1)
    def test_r05_c13_n_2(self): self.check(['-n', '2'], 'a\nb\nc\n', 'a\nb\n', 0)
    def test_r05_c14_no_arguments(self): self.check([], '1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n12\n', '1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n', 0)
    def test_r05_c15_c_3(self): self.check(['-c', '3'], 'ab', 'ab', 0)
    def test_r05_c16_n_2(self): self.check(['-n', '2'], 'one\ntwo', 'one\ntwo', 0)
    def test_r05_c17_empty_txt(self): self.check(['empty.txt'], None, '', 0)
    def test_r05_c18_n_1_empty_txt_fruits_txt(self): self.check(['-n', '1', 'empty.txt', 'fruits.txt'], None, '==> empty.txt <==\n\n==> fruits.txt <==\nbanana\n', 0)
    def test_r05_c19_c_2_left_txt_right_txt(self): self.check(['-c', '2', 'left.txt', 'right.txt'], None, '==> left.txt <==\nap\n==> right.txt <==\nba', 0)
    def test_r05_c20_n_3(self): self.check(['-n', '3'], '\n\n\n\n', '\n\n\n', 0)
