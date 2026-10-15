import unittest
import fileutil

class TestSafeFilename(unittest.TestCase):
    def test_r05_examples_from_the_spec(self):
        for src, want in [("report 2024.pdf", "report_2024.pdf"), ("../etc/passwd", "_etc_passwd"), ("naïve.txt", "na_ve.txt"), ("...", "unnamed"), ("", "unnamed"), ("a  b", "a__b")]:
            self.assertEqual(fileutil.safe_filename(src), want, src)
    def test_r05_allowed_characters_are_kept(self): self.assertEqual(fileutil.safe_filename("My-File_v1.2.TXT"), "My-File_v1.2.TXT")
    def test_r05_every_bad_character_becomes_its_own_underscore(self): self.assertEqual(fileutil.safe_filename("a:b*c?d"), "a_b_c_d"); self.assertEqual(fileutil.safe_filename("x///y"), "x___y")
    def test_r05_leading_dots_are_removed_but_inner_dots_stay(self): self.assertEqual(fileutil.safe_filename(".hidden.cfg"), "hidden.cfg"); self.assertEqual(fileutil.safe_filename("a..b"), "a..b")
    def test_r05_dots_are_removed_after_replacement(self): self.assertEqual(fileutil.safe_filename(".. x"), "_x")
    def test_r05_length_is_limited_to_255(self):
        self.assertEqual(fileutil.safe_filename("a" * 300), "a" * 255); self.assertEqual(len(fileutil.safe_filename("é" * 400)), 255)
    def test_r05_exactly_255_is_untouched(self): self.assertEqual(fileutil.safe_filename("b" * 255), "b" * 255)
    def test_r05_only_dots_and_nothing_left(self): self.assertEqual(fileutil.safe_filename("....."), "unnamed")
    def test_r05_a_single_replaced_character_is_not_empty(self): self.assertEqual(fileutil.safe_filename("?"), "_")

class TestSplitExt(unittest.TestCase):
    def test_r06_examples_from_the_spec(self):
        for src, want in [("archive.tar.gz", ("archive.tar", ".gz")), ("README", ("README", "")), (".bashrc", (".bashrc", "")), ("a.", ("a.", "")), ("photo.JPG", ("photo", ".JPG"))]:
            self.assertEqual(fileutil.split_ext(src), want, src)
    def test_r06_returns_a_tuple(self): self.assertIs(type(fileutil.split_ext("a.b")), tuple)
    def test_r06_hidden_file_with_extension(self): self.assertEqual(fileutil.split_ext(".config.json"), (".config", ".json"))
    def test_r06_empty_name(self): self.assertEqual(fileutil.split_ext(""), ("", ""))
    def test_r06_only_the_last_dot_counts(self): self.assertEqual(fileutil.split_ext("a.b.c.d"), ("a.b.c", ".d"))
    def test_r06_two_leading_dots(self): self.assertEqual(fileutil.split_ext("..x"), (".", ".x"))
    def test_r06_slash_raises_value_error(self):
        for bad in ("dir/file.txt", "/abs.txt", "a/"):
            with self.assertRaises(ValueError): fileutil.split_ext(bad)
