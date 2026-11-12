"""Hidden tests of basename.py. Every expected output and exit status was printed by the real `basename` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestBasename(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "basename.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"basename.py {args} with standard input {stdin!r}")
    def test_r15_c01_usr_bin_sort(self): self.check(['/usr/bin/sort'], None, 'sort\n', 0)
    def test_r15_c02_dir_file_txt(self): self.check(['dir/file.txt'], None, 'file.txt\n', 0)
    def test_r15_c03_file_txt(self): self.check(['file.txt'], None, 'file.txt\n', 0)
    def test_r15_c04_usr_lib(self): self.check(['/usr/lib/'], None, 'lib\n', 0)
    def test_r15_c05_no_arguments(self): self.check(['/'], None, '/\n', 0)
    def test_r15_c06_no_arguments(self): self.check(['///'], None, '/\n', 0)
    def test_r15_c07_a_b_c(self): self.check(['a/b/c//'], None, 'c\n', 0)
    def test_r15_c08_archive_tar_gz_gz(self): self.check(['archive.tar.gz', '.gz'], None, 'archive.tar\n', 0)
    def test_r15_c09_archive_tar_gz_tar(self): self.check(['archive.tar.gz', '.tar'], None, 'archive.tar.gz\n', 0)
    def test_r15_c10_txt_txt(self): self.check(['.txt', '.txt'], None, '.txt\n', 0)
    def test_r15_c11_dir_readme_me(self): self.check(['dir/readme', 'me'], None, 'read\n', 0)
    def test_r15_c12_dir_x_py_x_py(self): self.check(['dir/x.py', 'x.py'], None, 'x.py\n', 0)
    def test_r15_c13_a_b_c_d(self): self.check(['a b/c d'], None, 'c d\n', 0)
    def test_r15_c14_hidden(self): self.check(['.hidden'], None, '.hidden\n', 0)
    def test_r15_c15_dir_d(self): self.check(['dir.d/'], None, 'dir.d\n', 0)
