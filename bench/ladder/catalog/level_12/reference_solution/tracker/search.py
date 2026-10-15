"""The search language: `project:ABC status:open,in_progress assignee:alice label:bug label:ui priority:high reporter:bob word word`."""
import re

PRIORITIES = ("low", "medium", "high", "critical")
FIELDS = ("project", "status", "assignee", "reporter", "priority", "label")

class QueryError(ValueError):
    pass

def parse(text):
    """-> (filters, words). Repeated `label:` filters are all required; `status:` and `priority:` accept comma separated alternatives."""
    filters, words = {}, []
    for tok in str(text or "").split():
        if ":" not in tok:
            words.append(tok.lower()); continue
        key, _, value = tok.partition(":"); key = key.lower()
        if key not in FIELDS: raise QueryError(f"unknown search field '{key}'")
        if not value: raise QueryError(f"empty value for '{key}'")
        if key == "label": filters.setdefault("label", []).append(value.lower())
        elif key in ("status", "priority"): filters[key] = [v.lower() for v in value.split(",") if v]
        elif key == "project": filters[key] = value.upper()
        else: filters[key] = value.lower()
    return filters, words

def build_sql(filters, words, visible_projects):
    """WHERE clause and parameters over the `issues` table (alias i). `visible_projects` is a list of keys, or None for all."""
    where, args = [], []
    if visible_projects is not None:
        if not visible_projects: return "0", []
        where.append(f"i.project_key IN ({','.join('?' * len(visible_projects))})"); args += list(visible_projects)
    if "project" in filters: where.append("i.project_key = ?"); args.append(filters["project"])
    if "status" in filters: where.append(f"i.status IN ({','.join('?' * len(filters['status']))})"); args += filters["status"]
    if "priority" in filters: where.append(f"i.priority IN ({','.join('?' * len(filters['priority']))})"); args += filters["priority"]
    for name, col in (("assignee", "assignee_id"), ("reporter", "reporter_id")):
        if name in filters:
            if filters[name] == "none" and name == "assignee": where.append("i.assignee_id IS NULL")
            else: where.append(f"i.{col} = (SELECT id FROM users WHERE name = ? COLLATE NOCASE)"); args.append(filters[name])
    for label in filters.get("label", []): where.append("EXISTS (SELECT 1 FROM labels l WHERE l.issue_id = i.id AND l.label = ?)"); args.append(label)
    for w in words:
        where.append("(lower(i.title) LIKE ? ESCAPE '\\' OR lower(i.description) LIKE ? ESCAPE '\\')"); like = "%" + re.sub(r"([%_\\])", r"\\\1", w) + "%"; args += [like, like]
    return (" AND ".join(where) or "1"), args

SORTS = {"created": "i.created", "updated": "i.updated", "priority": "CASE i.priority WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END", "number": "i.number"}

def order_by(sort):
    desc = sort.startswith("-"); name = sort.lstrip("-+")
    if name not in SORTS: raise QueryError(f"unknown sort '{sort}' (use one of {', '.join(SORTS)}, optionally with a leading '-')")
    return f"{SORTS[name]} {'DESC' if desc else 'ASC'}, i.id {'DESC' if desc else 'ASC'}"
