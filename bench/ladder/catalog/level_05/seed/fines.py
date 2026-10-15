"""Late fines."""
from models import FINE_CENTS_PER_DAY

def days_late(due_day, return_day):
    """Whole days a book is late. Returned on or before the due day: 0."""
    late = return_day - due_day
    return late + 1 if late >= 0 else 0

def fine_cents(tier, due_day, return_day):
    return days_late(due_day, return_day) * FINE_CENTS_PER_DAY[tier]
