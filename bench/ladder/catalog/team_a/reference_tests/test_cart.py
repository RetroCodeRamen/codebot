import unittest
import cart

CAT = "apple: 0.50\npear: 1.20\nGreen Tea: 12.00\nlaptop: 1500"

class TestParseItems(unittest.TestCase):
    def test_r05_example_from_the_spec(self): self.assertEqual(cart.parse_items("apple x2, pear, apple"), [("apple", 3), ("pear", 1)])
    def test_r05_empty_and_blank_text(self): self.assertEqual(cart.parse_items(""), []); self.assertEqual(cart.parse_items("   "), [])
    def test_r05_result_is_a_list_of_tuples(self):
        r = cart.parse_items("pear"); self.assertIs(type(r), list); self.assertIs(type(r[0]), tuple); self.assertEqual(r, [("pear", 1)])
    def test_r05_names_are_lowercased_and_merged(self): self.assertEqual(cart.parse_items("Apple, APPLE x4"), [("apple", 5)])
    def test_r05_order_of_first_appearance(self): self.assertEqual(cart.parse_items("pear, apple, pear x2, kiwi"), [("pear", 3), ("apple", 1), ("kiwi", 1)])
    def test_r05_names_with_spaces(self): self.assertEqual(cart.parse_items("green tea x3"), [("green tea", 3)])
    def test_r05_quantity_with_several_digits(self): self.assertEqual(cart.parse_items("apple x120"), [("apple", 120)])
    def test_r05_spaces_around_entries(self): self.assertEqual(cart.parse_items("  apple   x2 ,   pear  "), [("apple", 2), ("pear", 1)])
    def test_r05_empty_entry_raises(self):
        for bad in ("apple,,pear", "apple,", ",apple"):
            with self.assertRaises(ValueError, msg=bad): cart.parse_items(bad)
    def test_r05_quantity_zero_raises(self):
        with self.assertRaises(ValueError): cart.parse_items("apple x0")

class TestSubtotal(unittest.TestCase):
    def test_r06_example_from_the_spec(self): self.assertEqual(cart.subtotal("apple: 0.50\npear: 1.20", "apple x2, pear"), 220)
    def test_r06_empty_list_is_zero(self): self.assertEqual(cart.subtotal(CAT, ""), 0)
    def test_r06_names_match_without_regard_to_case(self): self.assertEqual(cart.subtotal(CAT, "GREEN TEA x2, Laptop"), 152400)
    def test_r06_repeated_entries_are_added(self): self.assertEqual(cart.subtotal(CAT, "pear, pear, pear x2"), 480)
    def test_r06_unknown_item_raises_key_error(self):
        with self.assertRaises(KeyError): cart.subtotal(CAT, "apple, mango")
    def test_r06_bad_catalog_raises_value_error(self):
        with self.assertRaises(ValueError): cart.subtotal("apple 0.50", "apple")
    def test_r06_bad_list_raises_value_error(self):
        with self.assertRaises(ValueError): cart.subtotal(CAT, "apple x0")

class TestDiscount(unittest.TestCase):
    def test_r07_examples_from_the_spec(self):
        self.assertEqual(cart.discount(225, "save10"), 23); self.assertEqual(cart.discount(2000, "FIVEOFF"), 500); self.assertEqual(cart.discount(1999, "FIVEOFF"), 0)
    def test_r07_empty_code(self): self.assertEqual(cart.discount(5000, ""), 0); self.assertEqual(cart.discount(5000, "   "), 0)
    def test_r07_save10_rounds_to_the_nearest_cent(self):
        for sub, want in [(1000, 100), (224, 22), (226, 23), (5, 1), (4, 0), (0, 0), (15, 2), (25, 3)]: self.assertEqual(cart.discount(sub, "SAVE10"), want, sub)
    def test_r07_codes_ignore_case_and_spaces(self): self.assertEqual(cart.discount(1000, "  Save10 "), 100); self.assertEqual(cart.discount(2500, " fiveoff"), 500)
    def test_r07_fiveoff_above_the_limit(self): self.assertEqual(cart.discount(999999, "FIVEOFF"), 500)
    def test_r07_unknown_code_raises(self):
        for bad in ("SAVE20", "FREE", "SAVE 10"):
            with self.assertRaises(ValueError, msg=bad): cart.discount(1000, bad)
    def test_r07_result_is_an_int(self): self.assertIs(type(cart.discount(225, "SAVE10")), int)

class TestReceipt(unittest.TestCase):
    def test_r08_example_from_the_spec(self): self.assertEqual(cart.receipt("apple: 0.50", "apple x2"), "2 x apple @ $0.50 = $1.00\nSubtotal: $1.00\nTotal: $1.00")
    def test_r08_several_products_in_list_order(self):
        self.assertEqual(cart.receipt(CAT, "pear, apple x3"), "1 x pear @ $1.20 = $1.20\n3 x apple @ $0.50 = $1.50\nSubtotal: $2.70\nTotal: $2.70")
    def test_r08_discount_line_when_there_is_a_discount(self):
        self.assertEqual(cart.receipt(CAT, "green tea x2", "SAVE10"), "2 x green tea @ $12.00 = $24.00\nSubtotal: $24.00\nDiscount: -$2.40\nTotal: $21.60")
    def test_r08_no_discount_line_when_the_code_gives_nothing(self):
        self.assertEqual(cart.receipt(CAT, "pear", "FIVEOFF"), "1 x pear @ $1.20 = $1.20\nSubtotal: $1.20\nTotal: $1.20")
    def test_r08_thousands_separator_and_fiveoff(self):
        self.assertEqual(cart.receipt(CAT, "laptop", "fiveoff"), "1 x laptop @ $1,500.00 = $1,500.00\nSubtotal: $1,500.00\nDiscount: -$5.00\nTotal: $1,495.00")
    def test_r08_merged_entries_are_one_line(self):
        self.assertEqual(cart.receipt(CAT, "apple, apple"), "2 x apple @ $0.50 = $1.00\nSubtotal: $1.00\nTotal: $1.00")
    def test_r08_empty_list(self): self.assertEqual(cart.receipt(CAT, ""), "Subtotal: $0.00\nTotal: $0.00")
    def test_r08_no_trailing_newline(self): self.assertFalse(cart.receipt(CAT, "pear").endswith("\n"))
    def test_r08_unknown_item_and_unknown_code(self):
        with self.assertRaises(KeyError): cart.receipt(CAT, "mango")
        with self.assertRaises(ValueError): cart.receipt(CAT, "pear", "NOPE")
