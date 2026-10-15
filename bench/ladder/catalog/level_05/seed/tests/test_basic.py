import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from library import Library
from models import LoanError
from money import format_money

class TestBasic(unittest.TestCase):
    def setUp(self):
        self.lib = Library(); self.lib.add_book("111", "Python Basics", 2); self.lib.add_book("222", "Rust Guide", 1); self.lib.add_member("m1", "Ann"); self.lib.add_member("m2", "Bob", "premium")
    def test_checkout_and_return(self):
        loan = self.lib.checkout("m1", "111", 1); self.assertEqual(loan.due_day, 15); self.assertEqual(self.lib.books["111"].available, 1)
        self.assertEqual(self.lib.return_book("m1", "111", 5), 0); self.assertEqual(self.lib.books["111"].available, 2)
    def test_no_copy_left(self):
        self.lib.checkout("m1", "222", 1)
        with self.assertRaises(LoanError): self.lib.checkout("m2", "222", 2)
    def test_money(self): self.assertEqual(format_money(1050), "$10.50"); self.assertEqual(format_money(1234), "$12.34")
    def test_search_finds_exact_case(self): self.assertEqual([b.isbn for b in self.lib.search("Rust")], ["222"])

if __name__ == "__main__": unittest.main()
