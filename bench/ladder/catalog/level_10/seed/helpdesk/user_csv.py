"""Import and export of users as CSV (columns: name, role, email, active)."""
import csv
import io

from .errors import HelpdeskError

COLUMNS = ["name", "role", "email", "active"]


def export_users(store):
    """CSV text of all users, ordered by id."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for user in sorted(store.users.values(), key=lambda u: u.id):
        writer.writerow([user.name, user.role, user.email, "yes" if user.active else "no"])
    return buf.getvalue()


def parse_bool(text):
    value = (text or "").strip().lower()
    if value in ("", "yes", "true", "1"):
        return True
    if value in ("no", "false", "0"):
        return False
    raise ValueError(f"invalid active flag '{text}'")


def import_users(users, text):
    """Create the users described by the CSV text. Returns (created_ids, errors); rows with problems are reported and skipped."""
    created, errors = [], []
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "name" not in reader.fieldnames:
        raise ValueError("the CSV needs a 'name' column")
    for number, row in enumerate(reader, start=2):
        try:
            active = parse_bool(row.get("active"))
            user = users.create_user((row.get("name") or "").strip(), (row.get("role") or "customer").strip() or "customer", (row.get("email") or "").strip())
            if not active:
                user.active = False
                users.store.save()
            created.append(user.id)
        except (HelpdeskError, ValueError) as exc:
            errors.append(f"row {number}: {exc}")
    return created, errors
