import unittest
import catalog

class TestParseCatalog(unittest.TestCase):
    def test_r03_example_from_the_spec(self): self.assertEqual(catalog.parse_catalog("Apple: 0.50\nGreen Pear: $1.20"), {"apple": 50, "green pear": 120})
    def test_r03_empty_text_is_an_empty_catalog(self): self.assertEqual(catalog.parse_catalog(""), {})
    def test_r03_blank_lines_and_comments_are_ignored(self):
        self.assertEqual(catalog.parse_catalog("\n# prices\napple: 1\n\n   # more\n  pear: 2\n"), {"apple": 100, "pear": 200})
    def test_r03_split_at_the_first_colon_only(self):
        with self.assertRaises(ValueError): catalog.parse_catalog("tea: mint: 2")
    def test_r03_spaces_around_name_and_price(self): self.assertEqual(catalog.parse_catalog("   Milk  :   0.99  "), {"milk": 99})
    def test_r03_no_space_after_the_colon(self): self.assertEqual(catalog.parse_catalog("egg:0.3"), {"egg": 30})
    def test_r03_values_are_ints(self): self.assertIs(type(catalog.parse_catalog("a: 1.5")["a"]), int)
    def test_r03_line_without_colon(self):
        with self.assertRaises(ValueError): catalog.parse_catalog("apple 0.50")
    def test_r03_empty_name(self):
        with self.assertRaises(ValueError): catalog.parse_catalog(": 0.50")
    def test_r03_bad_price(self):
        for bad in ("apple: cheap", "apple: 1.234", "apple:"):
            with self.assertRaises(ValueError, msg=bad): catalog.parse_catalog(bad)
    def test_r03_duplicate_after_lowercasing(self):
        with self.assertRaises(ValueError): catalog.parse_catalog("Apple: 1\napple: 2")
    def test_r03_a_comment_sign_inside_a_line_is_not_a_comment(self):
        with self.assertRaises(ValueError): catalog.parse_catalog("apple: 1 # fresh")

class TestLookup(unittest.TestCase):
    def test_r04_example_from_the_spec(self): self.assertEqual(catalog.lookup({"apple": 50}, " APPLE "), 50)
    def test_r04_exact_name(self): self.assertEqual(catalog.lookup({"green pear": 120, "apple": 50}, "green pear"), 120)
    def test_r04_unknown_raises_key_error(self):
        with self.assertRaises(KeyError): catalog.lookup({"apple": 50}, "pear")
    def test_r04_empty_catalog(self):
        with self.assertRaises(KeyError): catalog.lookup({}, "apple")
    def test_r04_works_on_a_parsed_catalog(self): self.assertEqual(catalog.lookup(catalog.parse_catalog("Rye Bread: 3.25"), "RYE BREAD"), 325)
