# Helpdesk

A small ticketing system: users with roles, tickets with a life cycle, comments (public and internal), SLA deadlines, search, reports, notifications, exports and a command line.
Everything is plain Python 3 (standard library only); all data lives in one JSON file.

## Layout

| module | what it does |
|---|---|
| `models.py` | `User`, `Comment`, `Ticket` data classes with `to_dict` / `from_dict` |
| `store.py` | the JSON data file (`Store`): load, atomic save, id counters, lookups; `VERSION` is the file format version |
| `users.py`, `permissions.py` | user management and the role -> action table |
| `tickets.py` | `TicketService`: create, assign, status, priority, comments, watchers, attachments, listing with visibility rules |
| `workflow.py`, `sla.py`, `sla_calendar.py` | allowed status transitions, SLA deadlines, business-hours arithmetic |
| `search.py`, `saved_searches.py` | query language (`status:open assignee:3 words`) and stored queries |
| `reports.py`, `report_formats.py`, `stats.py`, `digest.py` | aggregations, rendering, statistics, notification digests |
| `escalation.py`, `bulk.py`, `templates.py`, `importer.py`, `retention.py`, `links.py`, `kb.py`, `settings.py` | operational helpers |
| `export.py` | CSV and JSON export (other tools read these files: do not rename or reorder existing columns/keys) |
| `app.py` | wires the services around one data file and one clock (`Helpdesk`) |
| `cli.py`, `__main__.py` | `python3 -m helpdesk ...` |

## Conventions

- Services take the acting user's id (`actor_id`); permissions are checked with `permissions.require`.
- Every mutating service call ends with `store.save()`; the store writes the whole document atomically.
- The clock is injected (`Helpdesk(path, clock)`); the command line honours `HELPDESK_NOW`, `HELPDESK_FILE` and `HELPDESK_USER`.
- Errors derive from `helpdesk.errors.HelpdeskError`; the command line maps `NotFound` to exit status 1, `PermissionDenied` to 3 and the rest to 2.
- Tests: `python3 -m unittest discover -s tests`.
