import unittest
import money

class TestParsePrice(unittest.TestCase):
    def test_r01_examples_from_the_spec(self):
        for text, want in [("12.34", 1234), ("5", 500), ("0.5", 50), (" $3.20 ", 320)]: self.assertEqual(money.parse_price(text), want, text)
    def test_r01_returns_an_int(self): self.assertIs(type(money.parse_price("1.10")), int)
    def test_r01_one_decimal_digit_is_tenths(self): self.assertEqual(money.parse_price("7.1"), 710); self.assertEqual(money.parse_price("7.10"), 710)
    def test_r01_leading_zero_cents(self): self.assertEqual(money.parse_price("3.05"), 305)
    def test_r01_zero(self): self.assertEqual(money.parse_price("0"), 0); self.assertEqual(money.parse_price("0.00"), 0)
    def test_r01_large_amounts_are_exact(self): self.assertEqual(money.parse_price("123456789.99"), 12345678999); self.assertEqual(money.parse_price("0.29"), 29)
    def test_r01_dollar_sign_without_spaces(self): self.assertEqual(money.parse_price("$10"), 1000)
    def test_r01_tabs_and_newlines_are_whitespace(self): self.assertEqual(money.parse_price("\t4.5\n"), 450)
    def test_r01_invalid_from_the_spec(self):
        for bad in ("1.234", "", "abc", "-1", ".5", "5.", "1.2.3"):
            with self.assertRaises(ValueError, msg=repr(bad)): money.parse_price(bad)
    def test_r01_two_dollar_signs_or_a_trailing_one(self):
        for bad in ("$$5", "5$", "$"):
            with self.assertRaises(ValueError, msg=repr(bad)): money.parse_price(bad)
    def test_r01_spaces_inside_and_other_notations(self):
        for bad in ("1 2", "1,50", "1e2", "+5", "   "):
            with self.assertRaises(ValueError, msg=repr(bad)): money.parse_price(bad)

class TestFormatPrice(unittest.TestCase):
    def test_r02_examples_from_the_spec(self):
        for cents, want in [(1234, "$12.34"), (5, "$0.05"), (0, "$0.00"), (123456789, "$1,234,567.89"), (-250, "-$2.50")]: self.assertEqual(money.format_price(cents), want)
    def test_r02_comma_only_from_a_thousand_dollars(self): self.assertEqual(money.format_price(99999), "$999.99"); self.assertEqual(money.format_price(100000), "$1,000.00")
    def test_r02_whole_dollars_keep_two_zeros(self): self.assertEqual(money.format_price(700), "$7.00")
    def test_r02_negative_cents_only(self): self.assertEqual(money.format_price(-5), "-$0.05")
    def test_r02_negative_with_commas(self): self.assertEqual(money.format_price(-123456), "-$1,234.56")
    def test_r02_float_raises_type_error(self):
        with self.assertRaises(TypeError): money.format_price(12.0)
    def test_r02_bool_raises_type_error(self):
        with self.assertRaises(TypeError): money.format_price(True)
    def test_r02_text_raises_type_error(self):
        with self.assertRaises(TypeError): money.format_price("1234")
    def test_r02_round_trip_with_parse_price(self):
        for cents in (1, 99, 100, 250, 99999): self.assertEqual(money.parse_price(money.format_price(cents)), cents)
