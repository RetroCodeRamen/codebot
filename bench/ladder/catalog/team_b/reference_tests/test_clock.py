import unittest
import clock

class TestParseTime(unittest.TestCase):
    def test_r01_examples_from_the_spec(self):
        for text, want in [("09:30", 570), ("00:00", 0), ("23:59", 1439), ("24:00", 1440)]: self.assertEqual(clock.parse_time(text), want, text)
    def test_r01_returns_an_int(self): self.assertIs(type(clock.parse_time("12:00")), int)
    def test_r01_noon_and_an_afternoon_time(self): self.assertEqual(clock.parse_time("12:00"), 720); self.assertEqual(clock.parse_time("17:45"), 1065)
    def test_r01_invalid_from_the_spec(self):
        for bad in ("9:30", "09:60", "24:01", "0930", " 09:30", ""):
            with self.assertRaises(ValueError, msg=repr(bad)): clock.parse_time(bad)
    def test_r01_hour_out_of_range(self):
        for bad in ("25:00", "24:30", "99:99"):
            with self.assertRaises(ValueError, msg=repr(bad)): clock.parse_time(bad)
    def test_r01_wrong_shape(self):
        for bad in ("09:3", "009:30", "09:30:00", "09-30", "ab:cd", "09:30 ", "-1:30"):
            with self.assertRaises(ValueError, msg=repr(bad)): clock.parse_time(bad)

class TestFormatTime(unittest.TestCase):
    def test_r02_examples_from_the_spec(self):
        for minutes, want in [(570, "09:30"), (0, "00:00"), (1440, "24:00")]: self.assertEqual(clock.format_time(minutes), want)
    def test_r02_zero_padding(self): self.assertEqual(clock.format_time(65), "01:05"); self.assertEqual(clock.format_time(1439), "23:59")
    def test_r02_round_trip(self):
        for m in (1, 59, 60, 61, 719, 720, 1439, 1440): self.assertEqual(clock.parse_time(clock.format_time(m)), m)
    def test_r02_out_of_range_raises_value_error(self):
        for bad in (-1, 1441, 100000):
            with self.assertRaises(ValueError, msg=bad): clock.format_time(bad)
    def test_r02_float_raises_type_error(self):
        with self.assertRaises(TypeError): clock.format_time(570.0)
    def test_r02_bool_raises_type_error(self):
        with self.assertRaises(TypeError): clock.format_time(True)
    def test_r02_text_raises_type_error(self):
        with self.assertRaises(TypeError): clock.format_time("570")

class TestParseDuration(unittest.TestCase):
    def test_r03_examples_from_the_spec(self):
        for text, want in [("2h", 120), ("45m", 45), ("1h30m", 90), ("15", 15)]: self.assertEqual(clock.parse_duration(text), want, text)
    def test_r03_minutes_alone_may_be_sixty_or_more(self): self.assertEqual(clock.parse_duration("90m"), 90); self.assertEqual(clock.parse_duration("120"), 120)
    def test_r03_surrounding_whitespace(self): self.assertEqual(clock.parse_duration("  1h5m \n"), 65)
    def test_r03_hours_with_zero_minutes(self): self.assertEqual(clock.parse_duration("2h0m"), 120); self.assertEqual(clock.parse_duration("0h30m"), 30)
    def test_r03_many_hours(self): self.assertEqual(clock.parse_duration("10h"), 600)
    def test_r03_invalid_from_the_spec(self):
        for bad in ("", "h", "1h60m", "30m1h", "1.5h"):
            with self.assertRaises(ValueError, msg=repr(bad)): clock.parse_duration(bad)
    def test_r03_zero_raises(self):
        for bad in ("0", "0m", "0h", "0h0m"):
            with self.assertRaises(ValueError, msg=repr(bad)): clock.parse_duration(bad)
    def test_r03_other_bad_forms(self):
        for bad in ("m", "1h 30m", "-5", "1hm", "abc", "1h30", "5 m"):
            with self.assertRaises(ValueError, msg=repr(bad)): clock.parse_duration(bad)
