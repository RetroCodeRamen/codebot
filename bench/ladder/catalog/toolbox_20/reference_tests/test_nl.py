"""Hidden tests of nl.py. Every expected output and exit status was printed by the real `nl` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestNl(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "nl.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"nl.py {args} with standard input {stdin!r}")
    def test_r11_c01_fruits_txt(self): self.check(['fruits.txt'], None, '     1\tbanana\n     2\tapple\n     3\tcherry\n     4\tapple\n     5\tBanana\n     6\tdate\n', 0)
    def test_r11_c02_words_txt(self): self.check(['words.txt'], None, '     1\tthe quick brown fox jumps over the lazy dog\n     2\tpack my box with five dozen liquor jugs\n       \n     3\thow vexingly quick daft zebras jump\n', 0)
    def test_r11_c03_b_a_words_txt(self): self.check(['-b', 'a', 'words.txt'], None, '     1\tthe quick brown fox jumps over the lazy dog\n     2\tpack my box with five dozen liquor jugs\n     3\t\n     4\thow vexingly quick daft zebras jump\n', 0)
    def test_r11_c04_ba_fruits_txt(self): self.check(['-ba', 'fruits.txt'], None, '     1\tbanana\n     2\tapple\n     3\tcherry\n     4\tapple\n     5\tBanana\n     6\tdate\n', 0)
    def test_r11_c05_left_txt_right_txt(self): self.check(['left.txt', 'right.txt'], None, '     1\tapple\n     2\tbanana\n     3\tcherry\n     4\tfig\n     5\tbanana\n     6\tcherry\n     7\tdate\n     8\tfig\n     9\tgrape\n', 0)
    def test_r11_c06_no_arguments(self): self.check([], 'one\n', '     1\tone\n', 0)
    def test_r11_c07_no_arguments(self): self.check([], '\n\n', '       \n       \n', 0)
    def test_r11_c08_b_a(self): self.check(['-b', 'a'], '\n\n', '     1\t\n     2\t\n', 0)
    def test_r11_c09_no_arguments(self): self.check([], '', '', 0)
    def test_r11_c10_no_arguments(self): self.check([], 'a\n \nb\n', '     1\ta\n     2\t \n     3\tb\n', 0)
    def test_r11_c11_b_a_empty_txt_left_txt(self): self.check(['-b', 'a', 'empty.txt', 'left.txt'], None, '     1\tapple\n     2\tbanana\n     3\tcherry\n     4\tfig\n', 0)
    def test_r11_c12_nums_txt(self): self.check(['nums.txt'], None, '     1\t10\n     2\t9\n     3\t-3\n     4\t2.5\n     5\t100\n     6\t9\n', 0)
