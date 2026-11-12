"""Hidden tests of rev.py. Every expected output and exit status was printed by the real `rev` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestRev(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "rev.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"rev.py {args} with standard input {stdin!r}")
    def test_r18_c01_fruits_txt(self): self.check(['fruits.txt'], None, 'ananab\nelppa\nyrrehc\nelppa\nananaB\netad\n', 0)
    def test_r18_c02_left_txt_right_txt(self): self.check(['left.txt', 'right.txt'], None, 'elppa\nananab\nyrrehc\ngif\nananab\nyrrehc\netad\ngif\neparg\n', 0)
    def test_r18_c03_no_arguments(self): self.check([], 'abc\n', 'cba\n', 0)
    def test_r18_c04_no_arguments(self): self.check([], 'a\n', 'a\n', 0)
    def test_r18_c05_no_arguments(self): self.check([], '', '', 0)
    def test_r18_c06_no_arguments(self): self.check([], '\n\n', '\n\n', 0)
    def test_r18_c07_no_arguments(self): self.check([], 'a b  c\n', 'c  b a\n', 0)
    def test_r18_c08_words_txt(self): self.check(['words.txt'], None, 'god yzal eht revo spmuj xof nworb kciuq eht\nsguj rouqil nezod evif htiw xob ym kcap\n\npmuj sarbez tfad kciuq ylgnixev woh\n', 0)
    def test_r18_c09_no_arguments(self): self.check([], '12345\n67\n', '54321\n76\n', 0)
    def test_r18_c10_empty_txt_left_txt(self): self.check(['empty.txt', 'left.txt'], None, 'elppa\nananab\nyrrehc\ngif\n', 0)
