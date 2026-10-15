"""Wiring: builds the store and all services around one data file and one clock."""
import os
from . import config
from .audit import AuditLog
from .bulk import BulkService
from .escalation import Escalator
from .importer import Importer
from . import settings as settings_mod
from .balancer import Balancer
from .kb import KnowledgeBase
from .links import LinkService
from .notifications import Notifier
from .retention import Retention
from .rules import RuleEngine
from .saved_searches import SavedSearches
from .store import Store
from .templates import Replier
from .tickets import TicketService
from .users import UserService
from .utils import system_clock

class Helpdesk:
    def __init__(self, path=None, clock=None):
        self.path = path or os.environ.get(config.FILE_ENV) or config.DEFAULT_FILE
        fixed = os.environ.get(config.NOW_ENV)
        self.clock = clock or ((lambda: fixed) if fixed else system_clock)
        settings_mod.apply(settings_mod.load(self.path + ".settings.json"))
        self.store = Store(self.path, self.clock)
        self.audit = AuditLog(self.store, self.clock)
        self.kb = KnowledgeBase(self.path + ".kb.json")
        self.notifier = Notifier(self.store, self.clock)
        self.users = UserService(self.store, self.clock, self.audit)
        self.tickets = TicketService(self.store, self.users, self.notifier, self.clock, self.audit)
        self.escalator = Escalator(self.store, self.tickets, self.clock)
        self.bulk = BulkService(self.store, self.tickets, self.clock)
        self.replier = Replier(self.store, self.tickets)
        self.importer = Importer(self.store, self.users, self.tickets)
        self.retention = Retention(self.store, self.clock, self.audit)
        self.links = LinkService(self.store, self.tickets, self.clock, self.audit)
        self.saved = SavedSearches(self.store, self.tickets)
        self.balancer = Balancer(self.store, self.tickets)
        self.rules = RuleEngine(self.store, self.users, self.tickets, self.path + ".rules.json")

def open_helpdesk(path=None, clock=None):
    return Helpdesk(path, clock)
