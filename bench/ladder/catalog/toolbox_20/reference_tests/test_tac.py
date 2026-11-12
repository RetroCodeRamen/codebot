"""Hidden tests of tac.py. Every expected output and exit status was printed by the real `tac` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestTac(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "tac.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"tac.py {args} with standard input {stdin!r}")
    def test_r17_c01_fruits_txt(self): self.check(['fruits.txt'], None, 'date\nBanana\napple\ncherry\napple\nbanana\n', 0)
    def test_r17_c02_left_txt_right_txt(self): self.check(['left.txt', 'right.txt'], None, 'fig\ncherry\nbanana\napple\ngrape\nfig\ndate\ncherry\nbanana\n', 0)
    def test_r17_c03_no_arguments(self): self.check([], 'a\nb\nc\n', 'c\nb\na\n', 0)
    def test_r17_c04_no_arguments(self): self.check([], 'only\n', 'only\n', 0)
    def test_r17_c05_no_arguments(self): self.check([], '', '', 0)
    def test_r17_c06_empty_txt_left_txt(self): self.check(['empty.txt', 'left.txt'], None, 'fig\ncherry\nbanana\napple\n', 0)
    def test_r17_c07_no_arguments(self): self.check([], '\n\nx\n', 'x\n\n\n', 0)
    def test_r17_c08_words_txt(self): self.check(['words.txt'], None, 'how vexingly quick daft zebras jump\n\npack my box with five dozen liquor jugs\nthe quick brown fox jumps over the lazy dog\n', 0)
    def test_r17_c09_nums_txt_fruits_txt(self): self.check(['nums.txt', 'fruits.txt'], None, '9\n100\n2.5\n-3\n9\n10\ndate\nBanana\napple\ncherry\napple\nbanana\n', 0)
    def test_r17_c10_no_arguments(self): self.check([], 'a\n\nb\n', 'b\n\na\n', 0)
