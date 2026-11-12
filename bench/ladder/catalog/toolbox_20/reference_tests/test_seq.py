"""Hidden tests of seq.py. Every expected output and exit status was printed by the real `seq` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestSeq(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "seq.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"seq.py {args} with standard input {stdin!r}")
    def test_r04_c01_5(self): self.check(['5'], None, '1\n2\n3\n4\n5\n', 0)
    def test_r04_c02_3_6(self): self.check(['3', '6'], None, '3\n4\n5\n6\n', 0)
    def test_r04_c03_1_2_9(self): self.check(['1', '2', '9'], None, '1\n3\n5\n7\n9\n', 0)
    def test_r04_c04_10_3_0(self): self.check(['10', '-3', '0'], None, '10\n7\n4\n1\n', 0)
    def test_r04_c05_5_1(self): self.check(['5', '1'], None, '', 0)
    def test_r04_c06_1_1_5(self): self.check(['1', '-1', '5'], None, '', 0)
    def test_r04_c07_s_5(self): self.check(['-s', ',', '5'], None, '1,2,3,4,5\n', 0)
    def test_r04_c08_s_2_4(self): self.check(['-s', ', ', '2', '4'], None, '2, 3, 4\n', 0)
    def test_r04_c09_w_8_12(self): self.check(['-w', '8', '12'], None, '08\n09\n10\n11\n12\n', 0)
    def test_r04_c10_w_98_101(self): self.check(['-w', '98', '101'], None, '098\n099\n100\n101\n', 0)
    def test_r04_c11_s_w_1_5_21(self): self.check(['-s', '-', '-w', '1', '5', '21'], None, '01-06-11-16-21\n', 0)
    def test_r04_c12_0(self): self.check(['0'], None, '', 0)
    def test_r04_c13_1(self): self.check(['1'], None, '1\n', 0)
    def test_r04_c14_2_2(self): self.check(['-2', '2'], None, '-2\n-1\n0\n1\n2\n', 0)
    def test_r04_c15_3_3(self): self.check(['3', '3'], None, '3\n', 0)
    def test_r04_c16_0_5_20(self): self.check(['0', '5', '20'], None, '0\n5\n10\n15\n20\n', 0)
    def test_r04_c17_5_5_10(self): self.check(['5', '-5', '-10'], None, '5\n0\n-5\n-10\n', 0)
    def test_r04_c18_s_3(self): self.check(['-s', '', '3'], None, '123\n', 0)
    def test_r04_c19_2_2_7(self): self.check(['2', '2', '7'], None, '2\n4\n6\n', 0)
    def test_r04_c20_s_x_1_1_1(self): self.check(['-s', 'x', '1', '1', '1'], None, '1\n', 0)
