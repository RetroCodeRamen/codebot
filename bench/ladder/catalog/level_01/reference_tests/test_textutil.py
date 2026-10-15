import unittest
import textutil

class TestSlugify(unittest.TestCase):
    def test_r01_examples_from_the_spec(self):
        for src, want in [("Hello World", "hello-world"), ("  Foo,  Bar!! ", "foo-bar"), ("a--b", "a-b"), ("Café au lait", "caf-au-lait"), ("", ""), ("---", "")]:
            self.assertEqual(textutil.slugify(src), want, src)
    def test_r01_digits_and_case(self): self.assertEqual(textutil.slugify("Python 3.12 RELEASE"), "python-3-12-release")
    def test_r01_leading_and_trailing_separators_are_removed(self): self.assertEqual(textutil.slugify("!!!abc???"), "abc")
    def test_r01_runs_collapse_to_one_hyphen(self): self.assertEqual(textutil.slugify("a \t\n_ -b"), "a-b")
    def test_r01_underscore_is_a_separator(self): self.assertEqual(textutil.slugify("snake_case_name"), "snake-case-name")
    def test_r01_non_ascii_letters_are_separators(self):
        self.assertEqual(textutil.slugify("naïve"), "na-ve"); self.assertEqual(textutil.slugify("日本語abc"), "abc"); self.assertEqual(textutil.slugify("１２３x"), "x")
    def test_r01_already_a_slug_is_unchanged(self): self.assertEqual(textutil.slugify("already-a-slug-42"), "already-a-slug-42")
    def test_r01_non_string_raises_type_error(self):
        for bad in (None, 5, b"abc", ["a"]):
            with self.assertRaises(TypeError): textutil.slugify(bad)

class TestTruncate(unittest.TestCase):
    def test_r02_examples_from_the_spec(self):
        self.assertEqual(textutil.truncate("hello world", 8), "hello..."); self.assertEqual(textutil.truncate("hello world", 2), ".."); self.assertEqual(textutil.truncate("hello", 0), "")
    def test_r02_short_text_is_unchanged(self):
        self.assertEqual(textutil.truncate("hi", 8), "hi"); self.assertEqual(textutil.truncate("exactly8", 8), "exactly8"); self.assertEqual(textutil.truncate("", 0), "")
    def test_r02_result_has_exactly_width_characters(self):
        for w in range(0, 12): self.assertEqual(len(textutil.truncate("abcdefghijklmnopqrstuvwxyz", w)), w)
    def test_r02_custom_suffix(self):
        self.assertEqual(textutil.truncate("hello world", 7, suffix="~"), "hello ~"); self.assertEqual(textutil.truncate("hello world", 7, suffix=""), "hello w")
    def test_r02_width_equal_to_suffix_length(self): self.assertEqual(textutil.truncate("hello world", 3), "...")
    def test_r02_width_smaller_than_custom_suffix(self): self.assertEqual(textutil.truncate("hello world", 1, suffix="[more]"), "[")
    def test_r02_negative_width_raises_value_error(self):
        with self.assertRaises(ValueError): textutil.truncate("abc", -1)
    def test_r02_one_over_the_limit_is_truncated(self): self.assertEqual(textutil.truncate("abcdefghi", 8), "abcde...")
