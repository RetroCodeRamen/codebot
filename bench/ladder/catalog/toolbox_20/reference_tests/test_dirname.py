"""Hidden tests of dirname.py. Every expected output and exit status was printed by the real `dirname` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestDirname(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "dirname.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"dirname.py {args} with standard input {stdin!r}")
    def test_r16_c01_usr_bin_sort(self): self.check(['/usr/bin/sort'], None, '/usr/bin\n', 0)
    def test_r16_c02_dir_file_txt(self): self.check(['dir/file.txt'], None, 'dir\n', 0)
    def test_r16_c03_file_txt(self): self.check(['file.txt'], None, '.\n', 0)
    def test_r16_c04_usr_lib(self): self.check(['/usr/lib/'], None, '/usr\n', 0)
    def test_r16_c05_no_arguments(self): self.check(['/'], None, '/\n', 0)
    def test_r16_c06_no_arguments(self): self.check(['///'], None, '/\n', 0)
    def test_r16_c07_a_b_c(self): self.check(['a/b/c//'], None, 'a/b\n', 0)
    def test_r16_c08_a_b(self): self.check(['a//b'], None, 'a\n', 0)
    def test_r16_c09_x(self): self.check(['/x'], None, '/\n', 0)
    def test_r16_c10_a_b_c_d_e(self): self.check(['a/b', 'c', '/d/e/'], None, 'a\n.\n/d\n', 0)
    def test_r16_c11_no_arguments(self): self.check(['.'], None, '.\n', 0)
    def test_r16_c12_no_arguments(self): self.check(['..'], None, '.\n', 0)
    def test_r16_c13_a(self): self.check(['a/'], None, '.\n', 0)
    def test_r16_c14_a(self): self.check(['//a'], None, '/\n', 0)
    def test_r16_c15_a_b_c_d(self): self.check(['a b/c d'], None, 'a b\n', 0)
