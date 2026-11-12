"""Hidden tests of b64.py. Every expected output and exit status was printed by the real `base64` (bench/toolbox/make.py)."""
import subprocess, sys, unittest

FIXTURES = {'fruits.txt': 'banana\napple\ncherry\napple\nBanana\ndate\n', 'nums.txt': '10\n9\n-3\n2.5\n100\n9\n', 'table.csv': 'name,qty,price\npear,3,1.20\nfig,12,0.45\nplain line\nkiwi,7,0.30\n', 'left.txt': 'apple\nbanana\ncherry\nfig\n', 'right.txt': 'banana\ncherry\ndate\nfig\ngrape\n', 'words.txt': 'the quick brown fox jumps over the lazy dog\npack my box with five dozen liquor jugs\n\nhow vexingly quick daft zebras jump\n', 'empty.txt': ''}

class TestB64(unittest.TestCase):
    maxDiff = None
    def check(self, args, stdin, want, status):
        for n, text in FIXTURES.items():
            with open(n, "w", newline="") as f: f.write(text)
        p = subprocess.run([sys.executable, "b64.py", *args], input=(stdin or "").encode("latin-1"), capture_output=True, timeout=20)
        self.assertEqual((p.stdout.decode("latin-1"), p.returncode), (want, status), f"b64.py {args} with standard input {stdin!r}")
    def test_r20_c01_no_arguments(self): self.check([], 'hello\n', 'aGVsbG8K\n', 0)
    def test_r20_c02_no_arguments(self): self.check([], 'a', 'YQ==\n', 0)
    def test_r20_c03_no_arguments(self): self.check([], 'ab', 'YWI=\n', 0)
    def test_r20_c04_no_arguments(self): self.check([], 'abc', 'YWJj\n', 0)
    def test_r20_c05_no_arguments(self): self.check([], '', '', 0)
    def test_r20_c06_fruits_txt(self): self.check(['fruits.txt'], None, 'YmFuYW5hCmFwcGxlCmNoZXJyeQphcHBsZQpCYW5hbmEKZGF0ZQo=\n', 0)
    def test_r20_c07_words_txt(self): self.check(['words.txt'], None, 'dGhlIHF1aWNrIGJyb3duIGZveCBqdW1wcyBvdmVyIHRoZSBsYXp5IGRvZwpwYWNrIG15IGJveCB3\naXRoIGZpdmUgZG96ZW4gbGlxdW9yIGp1Z3MKCmhvdyB2ZXhpbmdseSBxdWljayBkYWZ0IHplYnJh\ncyBqdW1wCg==\n', 0)
    def test_r20_c08_w_20_words_txt(self): self.check(['-w', '20', 'words.txt'], None, 'dGhlIHF1aWNrIGJyb3du\nIGZveCBqdW1wcyBvdmVy\nIHRoZSBsYXp5IGRvZwpw\nYWNrIG15IGJveCB3aXRo\nIGZpdmUgZG96ZW4gbGlx\ndW9yIGp1Z3MKCmhvdyB2\nZXhpbmdseSBxdWljayBk\nYWZ0IHplYnJhcyBqdW1w\nCg==\n', 0)
    def test_r20_c09_w_0_fruits_txt(self): self.check(['-w', '0', 'fruits.txt'], None, 'YmFuYW5hCmFwcGxlCmNoZXJyeQphcHBsZQpCYW5hbmEKZGF0ZQo=', 0)
    def test_r20_c10_w0(self): self.check(['-w0'], 'abc', 'YWJj', 0)
    def test_r20_c11_w_4(self): self.check(['-w', '4'], 'abcdefgh', 'YWJj\nZGVm\nZ2g=\n', 0)
    def test_r20_c12_d(self): self.check(['-d'], 'aGVsbG8K\n', 'hello\n', 0)
    def test_r20_c13_d(self): self.check(['-d'], 'YQ==\n', 'a', 0)
    def test_r20_c14_d(self): self.check(['-d'], 'YWJj', 'abc', 0)
    def test_r20_c15_d(self): self.check(['-d'], 'dGhlIHF1aWNr\nIGJyb3duIGZv\neAo=\n', 'the quick brown fox\n', 0)
    def test_r20_c16_d(self): self.check(['-d'], '', '', 0)
