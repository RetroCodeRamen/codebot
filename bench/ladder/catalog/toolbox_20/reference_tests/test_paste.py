"""Hidden tests of paste.py. Every expected output and exit status was printed by the real `paste` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestPaste(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "paste.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"paste.py {args} with standard input {stdin!r}")
    def test_r12_c01_left_txt_right_txt(self): self.check(['left.txt', 'right.txt'], None, 'apple\tbanana\nbanana\tcherry\ncherry\tdate\nfig\tfig\n\tgrape\n', 0)
    def test_r12_c02_right_txt_left_txt(self): self.check(['right.txt', 'left.txt'], None, 'banana\tapple\ncherry\tbanana\ndate\tcherry\nfig\tfig\ngrape\t\n', 0)
    def test_r12_c03_d_left_txt_right_txt(self): self.check(['-d', ',', 'left.txt', 'right.txt'], None, 'apple,banana\nbanana,cherry\ncherry,date\nfig,fig\n,grape\n', 0)
    def test_r12_c04_d_left_txt_right_txt_fruits_txt(self): self.check(['-d:', 'left.txt', 'right.txt', 'fruits.txt'], None, 'apple:banana:banana\nbanana:cherry:apple\ncherry:date:cherry\nfig:fig:apple\n:grape:Banana\n::date\n', 0)
    def test_r12_c05_s_left_txt(self): self.check(['-s', 'left.txt'], None, 'apple\tbanana\tcherry\tfig\n', 0)
    def test_r12_c06_s_left_txt_right_txt(self): self.check(['-s', 'left.txt', 'right.txt'], None, 'apple\tbanana\tcherry\tfig\nbanana\tcherry\tdate\tfig\tgrape\n', 0)
    def test_r12_c07_s_d_left_txt_right_txt(self): self.check(['-s', '-d', ',', 'left.txt', 'right.txt'], None, 'apple,banana,cherry,fig\nbanana,cherry,date,fig,grape\n', 0)
    def test_r12_c08_sd_nums_txt(self): self.check(['-sd', '+', 'nums.txt'], None, '10+9+-3+2.5+100+9\n', 0)
    def test_r12_c09_left_txt(self): self.check(['left.txt'], None, 'apple\nbanana\ncherry\nfig\n', 0)
    def test_r12_c10_empty_txt_left_txt(self): self.check(['empty.txt', 'left.txt'], None, '\tapple\n\tbanana\n\tcherry\n\tfig\n', 0)
    def test_r12_c11_left_txt_empty_txt(self): self.check(['left.txt', 'empty.txt'], None, 'apple\t\nbanana\t\ncherry\t\nfig\t\n', 0)
    def test_r12_c12_s_empty_txt(self): self.check(['-s', 'empty.txt'], None, '\n', 0)
    def test_r12_c13_d_fruits_txt_nums_txt(self): self.check(['-d', ' ', 'fruits.txt', 'nums.txt'], None, 'banana 10\napple 9\ncherry -3\napple 2.5\nBanana 100\ndate 9\n', 0)
    def test_r12_c14_s_d_words_txt(self): self.check(['-s', '-d', ' ', 'words.txt'], None, 'the quick brown fox jumps over the lazy dog pack my box with five dozen liquor jugs  how vexingly quick daft zebras jump\n', 0)
    def test_r12_c15_fruits_txt_fruits_txt_fruits_txt(self): self.check(['fruits.txt', 'fruits.txt', 'fruits.txt'], None, 'banana\tbanana\tbanana\napple\tapple\tapple\ncherry\tcherry\tcherry\napple\tapple\tapple\nBanana\tBanana\tBanana\ndate\tdate\tdate\n', 0)
