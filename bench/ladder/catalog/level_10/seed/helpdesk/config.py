"""Constants shared by all modules. Nothing in here depends on other helpdesk modules."""
ROLES = ("admin", "agent", "customer")
STATUSES = ("open", "in_progress", "waiting", "resolved", "closed")
PRIORITIES = ("low", "normal", "high", "urgent")
CATEGORIES = ("general", "billing", "technical", "account")
SLA_HOURS = {"urgent": 4, "high": 8, "normal": 24, "low": 72}      # hours from creation until a ticket is overdue
DEFAULT_FILE = "helpdesk.json"
FILE_ENV = "HELPDESK_FILE"            # data file used by the command line
NOW_ENV = "HELPDESK_NOW"              # fixed clock (ISO timestamp) for the command line, used by tests
USER_ENV = "HELPDESK_USER"            # acting user id when --as is not given
TITLE_MAX = 120
BODY_MAX = 20000
