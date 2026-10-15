"""Data model of the lending library."""
from dataclasses import dataclass
from typing import Optional

LOAN_DAYS = 14                                   # a book is due 14 days after checkout
LIMITS = {"standard": 3, "premium": 5}           # maximum simultaneous loans per member tier
FINE_CENTS_PER_DAY = {"standard": 25, "premium": 10}

class LoanError(Exception):
    """A checkout or return that is not allowed."""

@dataclass
class Book:
    isbn: str
    title: str
    copies: int
    available: int = -1

    def __post_init__(self):
        if self.available < 0: self.available = self.copies

@dataclass
class Member:
    member_id: str
    name: str
    tier: str = "standard"

@dataclass
class Loan:
    member_id: str
    isbn: str
    out_day: int
    due_day: int
    returned_day: Optional[int] = None
    fine_cents: int = 0

    @property
    def active(self): return self.returned_day is None
