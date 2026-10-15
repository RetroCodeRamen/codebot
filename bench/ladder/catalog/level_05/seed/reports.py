"""Human-readable reports."""
from money import format_money

def member_statement(lib, member_id, day):
    m = lib.members[member_id]
    lines = [f"Statement for {m.name} ({m.tier})"]
    for l in sorted(lib.active_loans(member_id), key=lambda l: l.isbn):
        status = f"OVERDUE by {day - l.due_day} day(s)" if l.due_day < day else f"due day {l.due_day}"
        lines.append(f"  {lib.books[l.isbn].title}: {status}")
    lines.append(f"Fines paid so far: {format_money(lib.total_fines(member_id))}")
    return "\n".join(lines)

def catalogue_summary(lib):
    total = sum(b.copies for b in lib.books.values()); out = sum(b.copies - b.available for b in lib.books.values())
    return f"{len(lib.books)} titles, {total} copies, {out} on loan"
