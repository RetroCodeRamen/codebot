import unittest
import planner

DAY = "09:00-10:00 standup\n13:00-14:00 lunch"

class TestBusy(unittest.TestCase):
    def test_r06_example_from_the_spec(self): self.assertEqual(planner.busy("09:00-10:00 standup\n09:30-11:00 review\n13:00-14:00"), [(540, 660), (780, 840)])
    def test_r06_empty_schedule(self): self.assertEqual(planner.busy(""), []); self.assertEqual(planner.busy("\n\n"), [])
    def test_r06_comments_and_blank_lines(self): self.assertEqual(planner.busy("# monday\n\n  08:00-08:30 gym\n   # nothing else\n"), [(480, 510)])
    def test_r06_label_may_have_several_words_and_digits(self): self.assertEqual(planner.busy("10:00-11:00 call with team 2 at 10:30-ish"), [(600, 660)])
    def test_r06_spaces_around_the_hyphen(self): self.assertEqual(planner.busy("10:00 - 11:00 call"), [(600, 660)])
    def test_r06_result_is_sorted_and_merged(self): self.assertEqual(planner.busy("15:00-16:00 b\n09:00-10:00 a\n10:00-10:30 c"), [(540, 630), (900, 960)])
    def test_r06_line_without_a_span_raises(self):
        for bad in ("standup 09:00-10:00", "lunch", "09:00 standup"):
            with self.assertRaises(ValueError, msg=bad): planner.busy(bad)
    def test_r06_bad_span_raises(self):
        for bad in ("10:00-09:00 backwards", "9:00-10:00 short hour", "09:00-25:00 late"):
            with self.assertRaises(ValueError, msg=bad): planner.busy(bad)

class TestFreeSlots(unittest.TestCase):
    def test_r07_example_from_the_spec(self): self.assertEqual(planner.free_slots(DAY, "08:00", "17:00", "1h"), ["08:00-09:00", "10:00-13:00", "14:00-17:00"])
    def test_r07_empty_schedule_is_one_slot(self): self.assertEqual(planner.free_slots("", "09:00", "17:00", "30m"), ["09:00-17:00"])
    def test_r07_short_gaps_are_left_out(self): self.assertEqual(planner.free_slots(DAY, "08:30", "17:00", "1h"), ["10:00-13:00", "14:00-17:00"])
    def test_r07_a_gap_of_exactly_the_duration_counts(self): self.assertEqual(planner.free_slots(DAY, "08:00", "15:00", "60"), ["08:00-09:00", "10:00-13:00", "14:00-15:00"])
    def test_r07_appointments_outside_the_day_are_ignored(self):
        self.assertEqual(planner.free_slots("06:00-07:00 run\n20:00-21:00 film", "09:00", "17:00", "1h"), ["09:00-17:00"])
    def test_r07_appointments_crossing_the_edges_are_clipped(self):
        self.assertEqual(planner.free_slots("07:00-09:30 early\n16:00-19:00 late", "09:00", "17:00", "1h"), ["09:30-16:00"])
    def test_r07_a_fully_booked_day(self): self.assertEqual(planner.free_slots("08:00-18:00 workshop", "09:00", "17:00", "15m"), [])
    def test_r07_no_gap_long_enough(self): self.assertEqual(planner.free_slots("09:30-16:30 x", "09:00", "17:00", "1h"), [])
    def test_r07_overlapping_appointments(self): self.assertEqual(planner.free_slots("09:00-11:00 a\n10:00-12:00 b", "09:00", "13:00", "1h"), ["12:00-13:00"])
    def test_r07_day_until_midnight(self): self.assertEqual(planner.free_slots("22:00-23:00 x", "21:00", "24:00", "1h"), ["21:00-22:00", "23:00-24:00"])
    def test_r07_day_end_not_after_day_start(self):
        for a, b in (("17:00", "09:00"), ("09:00", "09:00")):
            with self.assertRaises(ValueError): planner.free_slots("", a, b, "1h")
    def test_r07_bad_arguments_raise_value_error(self):
        with self.assertRaises(ValueError): planner.free_slots("", "9:00", "17:00", "1h")
        with self.assertRaises(ValueError): planner.free_slots("", "09:00", "17:00", "0m")
        with self.assertRaises(ValueError): planner.free_slots("lunch", "09:00", "17:00", "1h")

class TestFirstSlot(unittest.TestCase):
    def test_r08_example_from_the_spec(self): self.assertEqual(planner.first_slot("08:00-09:30 call", "08:00", "17:00", "45m"), "09:30-10:15")
    def test_r08_free_day_starts_at_day_start(self): self.assertEqual(planner.first_slot("", "09:00", "17:00", "1h30m"), "09:00-10:30")
    def test_r08_skips_gaps_that_are_too_short(self): self.assertEqual(planner.first_slot("09:30-10:00 a\n10:20-11:00 b", "09:00", "17:00", "45m"), "11:00-11:45")
    def test_r08_none_when_nothing_fits(self): self.assertIsNone(planner.first_slot("09:00-16:30 x", "09:00", "17:00", "1h"))
    def test_r08_exact_fit_at_the_end_of_the_day(self): self.assertEqual(planner.first_slot("09:00-16:00 x", "09:00", "17:00", "1h"), "16:00-17:00")
    def test_r08_slot_has_exactly_the_duration(self): self.assertEqual(planner.first_slot(DAY, "08:00", "17:00", "20"), "08:00-08:20")
    def test_r08_clipped_appointment_at_the_start(self): self.assertEqual(planner.first_slot("07:00-09:15 x", "09:00", "17:00", "30m"), "09:15-09:45")
    def test_r08_day_end_not_after_day_start(self):
        with self.assertRaises(ValueError): planner.first_slot("", "17:00", "09:00", "1h")
