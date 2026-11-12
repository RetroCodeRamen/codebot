"""Hidden tests of comm.py. Every expected output and exit status was printed by the real `comm` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestComm(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "comm.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"comm.py {args} with standard input {stdin!r}")
    def test_r13_c01_left_txt_right_txt(self): self.check(['left.txt', 'right.txt'], None, 'apple\n\t\tbanana\n\t\tcherry\n\tdate\n\t\tfig\n\tgrape\n', 0)
    def test_r13_c02_right_txt_left_txt(self): self.check(['right.txt', 'left.txt'], None, '\tapple\n\t\tbanana\n\t\tcherry\ndate\n\t\tfig\ngrape\n', 0)
    def test_r13_c03_1_left_txt_right_txt(self): self.check(['-1', 'left.txt', 'right.txt'], None, '\tbanana\n\tcherry\ndate\n\tfig\ngrape\n', 0)
    def test_r13_c04_2_left_txt_right_txt(self): self.check(['-2', 'left.txt', 'right.txt'], None, 'apple\n\tbanana\n\tcherry\n\tfig\n', 0)
    def test_r13_c05_3_left_txt_right_txt(self): self.check(['-3', 'left.txt', 'right.txt'], None, 'apple\n\tdate\n\tgrape\n', 0)
    def test_r13_c06_12_left_txt_right_txt(self): self.check(['-12', 'left.txt', 'right.txt'], None, 'banana\ncherry\nfig\n', 0)
    def test_r13_c07_13_left_txt_right_txt(self): self.check(['-13', 'left.txt', 'right.txt'], None, 'date\ngrape\n', 0)
    def test_r13_c08_23_left_txt_right_txt(self): self.check(['-23', 'left.txt', 'right.txt'], None, 'apple\n', 0)
    def test_r13_c09_123_left_txt_right_txt(self): self.check(['-123', 'left.txt', 'right.txt'], None, '', 0)
    def test_r13_c10_left_txt_left_txt(self): self.check(['left.txt', 'left.txt'], None, '\t\tapple\n\t\tbanana\n\t\tcherry\n\t\tfig\n', 0)
    def test_r13_c11_empty_txt_left_txt(self): self.check(['empty.txt', 'left.txt'], None, '\tapple\n\tbanana\n\tcherry\n\tfig\n', 0)
    def test_r13_c12_left_txt_empty_txt(self): self.check(['left.txt', 'empty.txt'], None, 'apple\nbanana\ncherry\nfig\n', 0)
    def test_r13_c13_1_2_right_txt_left_txt(self): self.check(['-1', '-2', 'right.txt', 'left.txt'], None, 'banana\ncherry\nfig\n', 0)
    def test_r13_c14_empty_txt_empty_txt(self): self.check(['empty.txt', 'empty.txt'], None, '', 0)
    def test_r13_c15_3_left_txt_left_txt(self): self.check(['-3', 'left.txt', 'left.txt'], None, '', 0)
