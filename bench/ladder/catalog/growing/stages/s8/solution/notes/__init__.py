"""Notes application, stage 8: the code is a package, one module per concern."""
from .errors import AuthError, NotesError, NotFound, PermissionDenied, StoreError, ValidationError
from .store import NoteStore
from .sync import sync_remote
from .worker import Worker
from .http_api import make_server
from .cli import main
