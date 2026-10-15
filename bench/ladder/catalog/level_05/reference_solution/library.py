"""The lending library: catalogue, members and loans."""
from models import Book, Member, Loan, LoanError, LIMITS, LOAN_DAYS
from fines import fine_cents
from search import search_books

class Library:
    def __init__(self):
        self.books, self.members, self.loans = {}, {}, []

    # --- catalogue and members ---
    def add_book(self, isbn, title, copies=1):
        if isbn in self.books: raise ValueError(f"duplicate isbn {isbn}")
        if copies < 1: raise ValueError("copies must be at least 1")
        self.books[isbn] = Book(isbn, title, copies); return self.books[isbn]

    def add_member(self, member_id, name, tier="standard"):
        if member_id in self.members: raise ValueError(f"duplicate member {member_id}")
        if tier not in LIMITS: raise ValueError(f"unknown tier {tier}")
        self.members[member_id] = Member(member_id, name, tier); return self.members[member_id]

    def search(self, text):
        return search_books(self.books.values(), text)

    # --- loans ---
    def active_loans(self, member_id):
        return [l for l in self.loans if l.member_id == member_id and l.active]

    def checkout(self, member_id, isbn, day):
        member, book = self.members.get(member_id), self.books.get(isbn)
        if member is None: raise LoanError(f"unknown member {member_id}")
        if book is None: raise LoanError(f"unknown book {isbn}")
        if book.available < 1: raise LoanError(f"no copy of {isbn} available")
        mine = self.active_loans(member_id)
        if len(mine) >= LIMITS[member.tier]: raise LoanError(f"{member_id} has reached the limit of {LIMITS[member.tier]} loans")
        if any(l.isbn == isbn for l in mine): raise LoanError(f"{member_id} already has {isbn}")
        loan = Loan(member_id, isbn, day, day + LOAN_DAYS)
        self.loans.append(loan); book.available -= 1; return loan

    def return_book(self, member_id, isbn, day):
        """Returns the fine in cents (0 when on time)."""
        member = self.members.get(member_id)
        if member is None: raise LoanError(f"unknown member {member_id}")
        loan = next((l for l in self.loans if l.member_id == member_id and l.isbn == isbn and l.active), None)
        if loan is None: raise LoanError(f"{member_id} does not have {isbn}")
        loan.returned_day = day; loan.fine_cents = fine_cents(member.tier, loan.due_day, day)
        self.books[isbn].available += 1; return loan.fine_cents

    def overdue(self, day):
        """Loans past their due day on `day`, ordered by member id then isbn."""
        late = [l for l in self.loans if l.active and l.due_day < day]
        return sorted(late, key=lambda l: (l.member_id, l.isbn))

    def total_fines(self, member_id):
        return sum(l.fine_cents for l in self.loans if l.member_id == member_id)
