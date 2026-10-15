# Lending library

A small in-memory library system: a catalogue of books, members with a tier (`standard` or `premium`), loans, late fines and reports.

- `models.py` data classes and constants (loan period 14 days, loan limits per tier, fine per late day in cents)
- `fines.py` late-fine rules; `money.py` money formatting (amounts are integer cents)
- `search.py` catalogue search; `library.py` the `Library` class; `reports.py` text reports
- `tests/` the existing unit tests: `python3 -m unittest discover -s tests`

Behaviour in short: a loan is due `LOAN_DAYS` days after checkout; returning on the due day or earlier is free, every day after costs the tier's fine per day;
a member may hold at most `LIMITS[tier]` books at once; a book can only be returned by the member who has it.
