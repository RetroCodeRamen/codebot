"""The exceptions of the notes application."""

class NotesError(Exception): pass
class ValidationError(NotesError, ValueError): pass
class NotFound(NotesError, KeyError): pass
class StoreError(NotesError): pass
class AuthError(NotesError): pass
class PermissionDenied(NotesError): pass
