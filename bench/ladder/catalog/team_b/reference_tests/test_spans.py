import unittest
import spans

class TestParseSpan(unittest.TestCase):
    def test_r04_examples_from_the_spec(self):
        self.assertEqual(spans.parse_span("09:00-10:30"), (540, 630)); self.assertEqual(spans.parse_span("09:00 - 10:30"), (540, 630))
    def test_r04_returns_a_tuple(self): self.assertIs(type(spans.parse_span("01:00-02:00")), tuple)
    def test_r04_whole_day(self): self.assertEqual(spans.parse_span("00:00-24:00"), (0, 1440))
    def test_r04_one_minute(self): self.assertEqual(spans.parse_span("12:00-12:01"), (720, 721))
    def test_r04_end_not_after_start(self):
        for bad in ("10:00-09:00", "10:00-10:00"):
            with self.assertRaises(ValueError, msg=bad): spans.parse_span(bad)
    def test_r04_bad_time(self):
        for bad in ("9:00-10:00", "09:00-25:00", "09:00-10:60"):
            with self.assertRaises(ValueError, msg=bad): spans.parse_span(bad)
    def test_r04_missing_hyphen_or_part(self):
        for bad in ("09:00 10:00", "09:00", "09:00-", "-10:00", ""):
            with self.assertRaises(ValueError, msg=repr(bad)): spans.parse_span(bad)

class TestMerge(unittest.TestCase):
    def test_r05_example_from_the_spec(self): self.assertEqual(spans.merge([(600, 660), (540, 600), (700, 720)]), [(540, 660), (700, 720)])
    def test_r05_empty(self): self.assertEqual(spans.merge([]), [])
    def test_r05_single_span_comes_back_as_a_tuple(self):
        r = spans.merge([[60, 120]]); self.assertEqual(r, [(60, 120)]); self.assertIs(type(r[0]), tuple)
    def test_r05_overlapping_spans_join(self): self.assertEqual(spans.merge([(0, 100), (50, 150)]), [(0, 150)])
    def test_r05_a_span_inside_another_disappears(self): self.assertEqual(spans.merge([(0, 500), (100, 200), (300, 400)]), [(0, 500)])
    def test_r05_touching_spans_join_but_a_gap_of_one_minute_stays(self):
        self.assertEqual(spans.merge([(0, 60), (60, 120)]), [(0, 120)]); self.assertEqual(spans.merge([(0, 60), (61, 120)]), [(0, 60), (61, 120)])
    def test_r05_a_chain_joins_into_one(self): self.assertEqual(spans.merge([(300, 400), (100, 200), (200, 300), (390, 450)]), [(100, 450)])
    def test_r05_duplicates(self): self.assertEqual(spans.merge([(10, 20), (10, 20)]), [(10, 20)])
    def test_r05_result_is_sorted_by_start(self): self.assertEqual(spans.merge([(900, 960), (60, 120), (480, 540)]), [(60, 120), (480, 540), (900, 960)])
    def test_r05_input_is_not_changed(self):
        given = [(600, 660), (540, 600)]; spans.merge(given); self.assertEqual(given, [(600, 660), (540, 600)])
    def test_r05_returns_a_new_list(self):
        given = [(1, 2)]; self.assertIsNot(spans.merge(given), given)
