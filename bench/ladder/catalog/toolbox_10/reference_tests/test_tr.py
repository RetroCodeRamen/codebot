"""Hidden tests of tr.py. Every expected output and exit status was printed by the real `tr` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestTr(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "tr.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"tr.py {args} with standard input {stdin!r}")
    def test_r08_c01_abc_xyz(self): self.check(['abc', 'xyz'], 'aabbcc cab\n', 'xxyyzz zxy\n', 0)
    def test_r08_c02_a_z_a_z(self): self.check(['a-z', 'A-Z'], 'Mixed Case 123\n', 'MIXED CASE 123\n', 0)
    def test_r08_c03_a_z_a_z(self): self.check(['A-Z', 'a-z'], 'Mixed Case 123\n', 'mixed case 123\n', 0)
    def test_r08_c04_a_y_b_z(self): self.check(['a-y', 'b-z'], 'hello\n', 'ifmmp\n', 0)
    def test_r08_c05_elo_x(self): self.check(['elo', 'x'], 'hello\n', 'hxxxx\n', 0)
    def test_r08_c06_0_9(self): self.check(['0-9', '#'], 'tel 555-0199\n', 'tel ###-####\n', 0)
    def test_r08_c07_no_arguments(self): self.check([' ', '_'], 'a b c\n', 'a_b_c\n', 0)
    def test_r08_c08_n(self): self.check(['\\n', ' '], 'one\ntwo\nthree\n', 'one two three ', 0)
    def test_r08_c09_t(self): self.check(['\\t', ' '], 'a\tb\n', 'a b\n', 0)
    def test_r08_c10_d_0_9(self): self.check(['-d', '0-9'], 'a1b22c333\n', 'abc\n', 0)
    def test_r08_c11_d_n(self): self.check(['-d', '\\n'], 'one\ntwo\n', 'onetwo', 0)
    def test_r08_c12_d_a_c(self): self.check(['-d', 'a-c'], 'abcdabcd\n', 'dd\n', 0)
    def test_r08_c13_s_a(self): self.check(['-s', 'a'], 'aaa bb aa\n', 'a bb a\n', 0)
    def test_r08_c14_s_a_z(self): self.check(['-s', 'a-z'], 'aabbccdd  ee\n', 'abcd  e\n', 0)
    def test_r08_c15_s_n(self): self.check(['-s', '\\n'], 'a\n\n\nb\n', 'a\nb\n', 0)
    def test_r08_c16_ab_ba(self): self.check(['ab', 'ba'], 'abba\n', 'baab\n', 0)
    def test_r08_c17_a_c_x(self): self.check(['a-c', 'x'], 'abcabc\n', 'xxxxxx\n', 0)
    def test_r08_c18_d(self): self.check(['-d', ' '], ' s p a c e \n', 'space\n', 0)
    def test_r08_c19_a_b(self): self.check(['a', 'b'], '', '', 0)
    def test_r08_c20_s(self): self.check(['-s', ' '], 'no_runs\n', 'no_runs\n', 0)
