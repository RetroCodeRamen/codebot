"""Money is stored as integer cents and formatted for people here."""

def format_money(cents):
    """1050 -> '$10.50', 5 -> '$0.05', -250 -> '-$2.50'."""
    sign = "-" if cents < 0 else ""
    dollars, rest = divmod(abs(cents), 100)
    return f"{sign}${dollars}.{rest}"

def total(amounts):
    return sum(amounts)
