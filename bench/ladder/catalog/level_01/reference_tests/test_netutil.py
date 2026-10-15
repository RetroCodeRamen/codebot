import unittest
import netutil

class TestIsValidIpv4(unittest.TestCase):
    def test_r07_valid_examples_from_the_spec(self):
        for s in ("192.168.1.1", "0.0.0.0", "255.255.255.255"): self.assertIs(netutil.is_valid_ipv4(s), True, s)
    def test_r07_invalid_examples_from_the_spec(self):
        for s in ("256.1.1.1", "1.2.3", "1.2.3.4.5", "01.2.3.4", "1.2.3.4 ", "-1.2.3.4", ""): self.assertIs(netutil.is_valid_ipv4(s), False, repr(s))
    def test_r07_trailing_newline_is_invalid(self): self.assertIs(netutil.is_valid_ipv4("1.2.3.4\n"), False)
    def test_r07_leading_space_and_inner_space(self):
        for s in (" 1.2.3.4", "1. 2.3.4", "1.2.3 .4"): self.assertIs(netutil.is_valid_ipv4(s), False, repr(s))
    def test_r07_octet_boundaries(self):
        self.assertIs(netutil.is_valid_ipv4("255.255.255.256"), False); self.assertIs(netutil.is_valid_ipv4("0.0.0.255"), True); self.assertIs(netutil.is_valid_ipv4("1.2.3.255"), True)
    def test_r07_zero_octets_and_leading_zero_rules(self):
        self.assertIs(netutil.is_valid_ipv4("10.0.0.1"), True); self.assertIs(netutil.is_valid_ipv4("10.00.0.1"), False); self.assertIs(netutil.is_valid_ipv4("10.0.0.001"), False)
    def test_r07_empty_octets_and_wrong_separators(self):
        for s in ("1..2.3", ".1.2.3", "1.2.3.", "1,2,3,4", "1.2.3.4.", "1-2-3-4"): self.assertIs(netutil.is_valid_ipv4(s), False, s)
    def test_r07_non_digit_characters(self):
        for s in ("a.b.c.d", "1.2.3.4a", "+1.2.3.4", "1.2.3.0x4", "1e1.2.3.4"): self.assertIs(netutil.is_valid_ipv4(s), False, s)
    def test_r07_non_ascii_digits_are_rejected(self): self.assertIs(netutil.is_valid_ipv4("١.2.3.4"), False); self.assertIs(netutil.is_valid_ipv4("１.2.3.4"), False)
    def test_r07_non_strings_return_false_and_never_raise(self):
        for bad in (None, 1234, b"1.2.3.4", ["1.2.3.4"], 1.5): self.assertIs(netutil.is_valid_ipv4(bad), False)
    def test_r07_returns_a_real_bool(self): self.assertIs(type(netutil.is_valid_ipv4("1.2.3.4")), bool)

class TestIpv4ToInt(unittest.TestCase):
    def test_r08_examples_from_the_spec(self):
        for s, want in [("0.0.0.1", 1), ("1.0.0.0", 16777216), ("192.168.0.1", 3232235521), ("255.255.255.255", 4294967295)]: self.assertEqual(netutil.ipv4_to_int(s), want, s)
    def test_r08_zero_address(self): self.assertEqual(netutil.ipv4_to_int("0.0.0.0"), 0)
    def test_r08_byte_order(self): self.assertEqual(netutil.ipv4_to_int("1.2.3.4"), 0x01020304); self.assertEqual(netutil.ipv4_to_int("10.0.0.0"), 167772160)
    def test_r08_each_octet_position(self):
        for i, w in enumerate((1 << 24, 1 << 16, 1 << 8, 1)):
            parts = ["0"] * 4; parts[i] = "1"; self.assertEqual(netutil.ipv4_to_int(".".join(parts)), w)
    def test_r08_invalid_addresses_raise_value_error(self):
        for bad in ("256.0.0.0", "1.2.3", "01.2.3.4", "", "a.b.c.d", " 1.2.3.4", "1.2.3.4\n"):
            with self.assertRaises(ValueError): netutil.ipv4_to_int(bad)
    def test_r08_non_string_raises_value_error(self):
        with self.assertRaises(ValueError): netutil.ipv4_to_int(None)
    def test_r08_returns_an_int(self): self.assertIs(type(netutil.ipv4_to_int("9.9.9.9")), int)
