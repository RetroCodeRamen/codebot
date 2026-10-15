import unittest
from library import Library
from models import LoanError, LOAN_DAYS, LIMITS
import fines, money, reports, search

def lib(books=6):
    L = Library()
    for i in range(1, books + 1): L.add_book(f"b{i}", f"Title {chr(64 + i)}", 2)
    L.add_member("m1", "Ann"); L.add_member("m2", "Bob", "premium"); return L

class TestFines(unittest.TestCase):
    def test_r01_days_late_examples(self):
        self.assertEqual(fines.days_late(15, 15), 0); self.assertEqual(fines.days_late(15, 18), 3); self.assertEqual(fines.days_late(15, 14), 0); self.assertEqual(fines.days_late(15, 16), 1)
    def test_r01_early_return_is_free(self): self.assertEqual(fines.days_late(15, 1), 0)
    def test_r01_standard_member_three_days_late_pays_75(self):
        L = lib(); L.checkout("m1", "b1", 1); self.assertEqual(L.return_book("m1", "b1", 1 + LOAN_DAYS + 3), 75)
    def test_r01_premium_member_three_days_late_pays_30(self):
        L = lib(); L.checkout("m2", "b1", 1); self.assertEqual(L.return_book("m2", "b1", 1 + LOAN_DAYS + 3), 30)
    def test_r01_returned_on_the_due_day_is_free(self):
        L = lib(); L.checkout("m1", "b1", 1); self.assertEqual(L.return_book("m1", "b1", 15), 0); self.assertEqual(L.total_fines("m1"), 0)
    def test_r01_one_day_late_is_one_day_of_fine(self):
        L = lib(); L.checkout("m1", "b1", 1); self.assertEqual(L.return_book("m1", "b1", 16), 25)
    def test_r01_fine_cents_uses_days_late(self): self.assertEqual(fines.fine_cents("standard", 15, 15), 0); self.assertEqual(fines.fine_cents("standard", 15, 20), 125); self.assertEqual(fines.fine_cents("premium", 15, 20), 50)
    def test_r01_total_fines_adds_up_across_loans(self):
        L = lib(); L.checkout("m1", "b1", 1); L.checkout("m1", "b2", 1); L.return_book("m1", "b1", 17); L.return_book("m1", "b2", 16); self.assertEqual(L.total_fines("m1"), 50 + 25)

class TestLimit(unittest.TestCase):
    def test_r02_standard_limit_is_three(self):
        L = lib(); [L.checkout("m1", f"b{i}", 1) for i in (1, 2, 3)]
        with self.assertRaises(LoanError): L.checkout("m1", "b4", 1)
        self.assertEqual(len(L.active_loans("m1")), 3)
    def test_r02_premium_limit_is_five(self):
        L = lib(); [L.checkout("m2", f"b{i}", 1) for i in (1, 2, 3, 4, 5)]
        with self.assertRaises(LoanError): L.checkout("m2", "b6", 1)
    def test_r02_a_failed_checkout_changes_nothing(self):
        L = lib(); [L.checkout("m1", f"b{i}", 1) for i in (1, 2, 3)]; before = L.books["b4"].available
        with self.assertRaises(LoanError): L.checkout("m1", "b4", 1)
        self.assertEqual(L.books["b4"].available, before); self.assertEqual(len(L.loans), 3)
    def test_r02_returning_frees_a_slot(self):
        L = lib(); [L.checkout("m1", f"b{i}", 1) for i in (1, 2, 3)]; L.return_book("m1", "b2", 2); L.checkout("m1", "b4", 3); self.assertEqual(len(L.active_loans("m1")), 3)
    def test_r02_limit_message_names_the_limit(self):
        L = lib(); [L.checkout("m1", f"b{i}", 1) for i in (1, 2, 3)]
        with self.assertRaises(LoanError) as cm: L.checkout("m1", "b4", 1)
        self.assertIn("3", str(cm.exception))
    def test_r02_limits_are_per_member(self):
        L = lib(); [L.checkout("m1", f"b{i}", 1) for i in (1, 2, 3)]; L.checkout("m2", "b1", 1); self.assertEqual(len(L.active_loans("m2")), 1)
    def test_r02_exactly_at_the_limit_is_allowed(self):
        L = lib(); [L.checkout("m1", f"b{i}", 1) for i in (1, 2)]; L.checkout("m1", "b3", 1); self.assertEqual(len(L.active_loans("m1")), 3)

class TestSearch(unittest.TestCase):
    def setUp(self):
        self.L = Library()
        for isbn, t in (("1", "Python Basics"), ("2", "advanced PYTHON"), ("3", "Rust Guide"), ("4", "Learning python the hard way")): self.L.add_book(isbn, t, 1)
    def test_r03_lowercase_query_finds_mixed_case_titles(self): self.assertEqual([b.isbn for b in self.L.search("python")], ["4", "1", "2"])
    def test_r03_hits_ordered_by_title(self): self.assertEqual([b.title for b in self.L.search("python")], ["Learning python the hard way", "Python Basics", "advanced PYTHON"])
    def test_r03_uppercase_query(self): self.assertEqual(len(self.L.search("PYTHON")), 3)
    def test_r03_mixed_case_query(self): self.assertEqual([b.isbn for b in self.L.search("rUsT")], ["3"])
    def test_r03_substring_not_just_prefix(self): self.assertEqual([b.isbn for b in self.L.search("BASIC")], ["1"])
    def test_r03_no_match(self): self.assertEqual(self.L.search("haskell"), [])
    def test_r03_search_function_directly(self): self.assertEqual([b.isbn for b in search.search_books(self.L.books.values(), "GUIDE")], ["3"])

class TestDoubleReturn(unittest.TestCase):
    def test_r04_second_return_raises(self):
        L = lib(); L.checkout("m1", "b1", 1); L.return_book("m1", "b1", 5)
        with self.assertRaises(LoanError) as cm: L.return_book("m1", "b1", 6)
        self.assertEqual(str(cm.exception), "m1 does not have b1")
    def test_r04_availability_is_not_inflated(self):
        L = lib(); L.checkout("m1", "b1", 1); L.return_book("m1", "b1", 5)
        with self.assertRaises(LoanError): L.return_book("m1", "b1", 6)
        self.assertEqual(L.books["b1"].available, 2)
    def test_r04_the_loan_record_is_not_modified_by_the_failed_return(self):
        L = lib(); L.checkout("m1", "b1", 1); L.return_book("m1", "b1", 20); fine = L.loans[0].fine_cents
        with self.assertRaises(LoanError): L.return_book("m1", "b1", 40)
        self.assertEqual((L.loans[0].returned_day, L.loans[0].fine_cents), (20, fine))
    def test_r04_borrowing_again_after_a_return_works_and_can_be_returned(self):
        L = lib(); L.checkout("m1", "b1", 1); L.return_book("m1", "b1", 5); L.checkout("m1", "b1", 10); self.assertEqual(L.return_book("m1", "b1", 12), 0); self.assertEqual(L.books["b1"].available, 2)
    def test_r04_the_second_loan_is_the_one_returned(self):
        L = lib(); L.checkout("m1", "b1", 1); L.return_book("m1", "b1", 5); L.checkout("m1", "b1", 10); L.return_book("m1", "b1", 30)
        self.assertEqual([l.returned_day for l in L.loans], [5, 30]); self.assertEqual(L.loans[1].fine_cents, 25 * (30 - 24))
    def test_r04_returning_a_book_never_borrowed_raises(self):
        L = lib()
        with self.assertRaises(LoanError): L.return_book("m1", "b1", 3)
    def test_r04_another_member_cannot_return_it(self):
        L = lib(); L.checkout("m1", "b1", 1)
        with self.assertRaises(LoanError): L.return_book("m2", "b1", 2)
        self.assertEqual(len(L.active_loans("m1")), 1)

class TestMoney(unittest.TestCase):
    def test_r05_examples(self):
        for cents, want in ((1005, "$10.05"), (5, "$0.05"), (-5, "-$0.05"), (100, "$1.00"), (0, "$0.00"), (1050, "$10.50"), (99, "$0.99"), (101, "$1.01"), (-250, "-$2.50"), (123456, "$1234.56")): self.assertEqual(money.format_money(cents), want, cents)
    def test_r05_every_cent_value_has_two_digits(self):
        for c in range(0, 200): self.assertEqual(money.format_money(c), f"${c // 100}.{c % 100:02d}")
    def test_r05_statements_show_the_corrected_amounts(self):
        L = lib(); L.checkout("m1", "b1", 1); L.return_book("m1", "b1", 16 + 1)           # 2 days late = 50 cents
        L.checkout("m1", "b2", 1); L.return_book("m1", "b2", 15 + 0)
        self.assertIn("Fines paid so far: $0.50", reports.member_statement(L, "m1", 20))
    def test_r05_statement_with_a_single_cent_style_amount(self):
        L = Library(); L.add_book("x", "X", 1); L.add_member("p", "Pat", "premium"); L.checkout("p", "x", 1); L.return_book("p", "x", 16)       # 1 day late at 10 cents
        self.assertIn("Fines paid so far: $0.10", reports.member_statement(L, "p", 20))

class TestOverdue(unittest.TestCase):
    def test_r06_returned_loans_are_not_listed(self):
        L = lib(); L.checkout("m1", "b1", 1); L.checkout("m1", "b2", 1); L.return_book("m1", "b1", 30)
        self.assertEqual([(l.member_id, l.isbn) for l in L.overdue(31)], [("m1", "b2")])
    def test_r06_active_loans_past_due_are_listed(self): L = lib(); L.checkout("m1", "b1", 1); self.assertEqual(len(L.overdue(16)), 1)
    def test_r06_due_day_itself_is_not_overdue(self): L = lib(); L.checkout("m1", "b1", 1); self.assertEqual(L.overdue(15), []); self.assertEqual(len(L.overdue(16)), 1)
    def test_r06_ordered_by_member_then_isbn(self):
        L = lib(); L.checkout("m2", "b2", 1); L.checkout("m1", "b3", 1); L.checkout("m1", "b1", 1); L.checkout("m2", "b1", 1)
        self.assertEqual([(l.member_id, l.isbn) for l in L.overdue(30)], [("m1", "b1"), ("m1", "b3"), ("m2", "b1"), ("m2", "b2")])
    def test_r06_nothing_overdue_when_everything_was_returned(self):
        L = lib(); L.checkout("m1", "b1", 1); L.return_book("m1", "b1", 40); self.assertEqual(L.overdue(100), [])
    def test_r06_a_reborrowed_book_is_listed_once(self):
        L = lib(); L.checkout("m1", "b1", 1); L.return_book("m1", "b1", 5); L.checkout("m1", "b1", 10); self.assertEqual(len(L.overdue(40)), 1)

class TestRegression(unittest.TestCase):
    def test_r07_checkout_sets_the_due_day(self): L = lib(); self.assertEqual(L.checkout("m1", "b1", 3).due_day, 3 + LOAN_DAYS)
    def test_r07_availability_follows_loans(self):
        L = lib(); L.add_member("m3", "Cy"); L.checkout("m1", "b1", 1); L.checkout("m2", "b1", 1); self.assertEqual(L.books["b1"].available, 0)
        with self.assertRaises(LoanError): L.checkout("m3", "b1", 1)
    def test_r07_no_copy_available(self):
        L = Library(); L.add_book("x", "X", 1); L.add_member("a", "A"); L.add_member("b", "B"); L.checkout("a", "x", 1)
        with self.assertRaises(LoanError): L.checkout("b", "x", 2)
    def test_r07_unknown_member_and_book(self):
        L = lib()
        with self.assertRaises(LoanError): L.checkout("nobody", "b1", 1)
        with self.assertRaises(LoanError): L.checkout("m1", "nope", 1)
        with self.assertRaises(LoanError): L.return_book("nobody", "b1", 1)
    def test_r07_the_same_member_cannot_borrow_the_same_book_twice(self):
        L = lib(); L.checkout("m1", "b1", 1)
        with self.assertRaises(LoanError): L.checkout("m1", "b1", 2)
    def test_r07_duplicate_isbn_and_member(self):
        L = lib()
        with self.assertRaises(ValueError): L.add_book("b1", "Again", 1)
        with self.assertRaises(ValueError): L.add_member("m1", "Again")
        with self.assertRaises(ValueError): L.add_book("z", "Z", 0)
        with self.assertRaises(ValueError): L.add_member("q", "Q", "gold")
    def test_r07_search_empty_text_matches_everything_in_title_order(self): L = lib(3); self.assertEqual([b.title for b in L.search("")], ["Title A", "Title B", "Title C"])
    def test_r07_search_results_are_ordered_by_title(self): L = Library(); L.add_book("1", "Zed", 1); L.add_book("2", "Alpha", 1); self.assertEqual([b.isbn for b in L.search("")], ["2", "1"])
    def test_r07_statement_lists_active_loans(self):
        L = lib(); L.checkout("m1", "b2", 1); L.checkout("m1", "b1", 1); s = reports.member_statement(L, "m1", 5).splitlines()
        self.assertEqual(s[0], "Statement for Ann (standard)"); self.assertEqual(s[1], "  Title A: due day 15"); self.assertEqual(s[2], "  Title B: due day 15"); self.assertTrue(s[-1].startswith("Fines paid so far: $"))
    def test_r07_statement_marks_overdue_loans(self): L = lib(); L.checkout("m1", "b1", 1); self.assertIn("OVERDUE by 5 day(s)", reports.member_statement(L, "m1", 20))
    def test_r07_catalogue_summary(self): L = lib(2); L.checkout("m1", "b1", 1); self.assertEqual(reports.catalogue_summary(L), "2 titles, 4 copies, 1 on loan")
    def test_r07_limits_and_constants(self): self.assertEqual(LIMITS, {"standard": 3, "premium": 5}); self.assertEqual(LOAN_DAYS, 14)
    def test_r07_money_total(self): self.assertEqual(money.total([1, 2, 3]), 6)
    def test_r07_total_fines_of_a_member_without_loans(self): self.assertEqual(lib().total_fines("m1"), 0)
    def test_r07_loan_active_property(self): L = lib(); loan = L.checkout("m1", "b1", 1); self.assertTrue(loan.active); L.return_book("m1", "b1", 2); self.assertFalse(loan.active)
    def test_r07_active_loans_only_lists_the_members_own(self): L = lib(); L.checkout("m1", "b1", 1); L.checkout("m2", "b2", 1); self.assertEqual([l.isbn for l in L.active_loans("m1")], ["b1"])
    def test_r07_a_return_inside_the_loan_period_is_free_and_records_the_day(self): L = lib(); L.checkout("m1", "b1", 1); self.assertEqual(L.return_book("m1", "b1", 8), 0); self.assertEqual(L.loans[0].returned_day, 8)
    def test_r07_returning_restores_availability(self): L = lib(); L.checkout("m1", "b1", 1); L.return_book("m1", "b1", 2); self.assertEqual(L.books["b1"].available, 2)
    def test_r07_books_start_fully_available(self): self.assertEqual(lib().books["b1"].available, 2)
    def test_r07_search_ignores_nonmatching_books(self): self.assertEqual(lib().search("zzz"), [])
