# Fix six bugs in the lending library

The code directory contains a small, working lending-library application (read `README.md` first): a catalogue, members, loans, late fines and reports, with unit tests in `tests/`.
Six bugs have been reported by its users. Find and fix each one in the code. Python 3, standard library only, no network.
Keep the public interface exactly as it is (module, class, function and attribute names, argument lists, return types and the existing messages). Fix the causes, do not special-case the examples.
Everything that already works must keep working: the existing unit tests (`python3 -m unittest discover -s tests`) must still pass, and no behaviour other than the six below may change.

## Bug reports

1. Late fines are wrong. A book returned exactly on its due day is charged a fine, and a book returned late is charged one day too many. `fines.days_late(due_day, return_day)` must be `0` when the book is returned on or before its due day and otherwise the number of days after the due day (`days_late(15, 15)` is `0`, `days_late(15, 18)` is `3`). `Library.return_book` must therefore charge exactly `days_late` times the member tier's fine per day (a standard member returning 3 days late pays 75 cents, a premium member 30 cents).

2. Members can borrow one book more than they are allowed. A standard member can check out 4 books at the same time although the limit is 3 (premium: 5). The `LoanError` must be raised for the book that would exceed the limit, and the limit counts only books that are currently on loan: after returning a book the member can borrow again.

3. Searching is case-sensitive. `Library.search("python")` does not find the book titled `Python Basics`. The search must find every book whose title contains the text ignoring letter case, must still return the hits ordered by title, and an empty search still matches every book.

4. A book can be returned twice. After a successful return, returning the same book again for the same member silently succeeds and increases the number of available copies beyond the number the library owns. A return must only be accepted for a loan that is still active; otherwise `Library.return_book` raises `LoanError` (message: `<member id> does not have <isbn>`), changes nothing, and the same member can borrow that book again later and return it normally.

5. Small amounts are formatted wrongly. `money.format_money(1005)` returns `"$10.5"` but must return `"$10.05"`; amounts below one dollar and negative amounts have the same problem (`format_money(5)` must be `"$0.05"`, `format_money(-5)` must be `"-$0.05"`, `format_money(100)` must be `"$1.00"`). The statements built by `reports.member_statement` use this function and must show the corrected amounts.

6. The overdue report lists books that were already returned. `Library.overdue(day)` must list only loans that are still active and whose due day is before `day`, ordered by member id and then isbn, as before.
