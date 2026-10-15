"""NoteStore: the core store with accounts, reminders and synchronisation mixed in."""
from .accounts import AccountsMixin
from .core import StoreCore
from .reminders import RemindersMixin
from .sync import SyncMixin

class NoteStore(AccountsMixin, RemindersMixin, SyncMixin, StoreCore):
    """Everything a notes database can do; each concern lives in its own module."""
