# Notes application, stage 8: architecture refactoring (amendment)

The application has grown into one big file. Turn it into a package without changing any behaviour: every requirement of stages 1 to 7 stays in force, all their tests run again unchanged, and existing databases keep working. Python 3, standard library only.

## Requirements

1. A package instead of a module. The single file `notes.py` is replaced by the package `notes/` (a directory with `__init__.py`; the file `notes.py` must no longer exist). `python3 -m notes ...` keeps working (`notes/__main__.py`), and everything that could be imported from `notes` before still can: `NoteStore`, `NotesError`, `ValidationError`, `NotFound`, `StoreError`, `AuthError`, `PermissionDenied`, `Worker`, `make_server`, `sync_remote`, `main`.

2. Modules. The code lives in separate modules, each with one job. These names are required and these objects must be defined in them (so `notes.NoteStore` is `notes.store.NoteStore`, and so on): `notes.errors` (all exception classes), `notes.store` (`NoteStore`), `notes.http_api` (`make_server`), `notes.cli` (`main`), `notes.worker` (`Worker`), `notes.sync` (`sync_remote`). Besides these the package has at least four more modules of your choice, for example for validation, the database schema, accounts and sharing, reminders, or the shared core of the store. `notes/__init__.py` only re-exports names, it contains no logic (at most 25 lines).

3. Size and structure. No Python file of the package has more than 250 lines. Every module can be imported on its own in a fresh interpreter (`import notes.cli`, `import notes.sync`, ... each work without importing the package first in a special order, so no import cycles that break), and importing the package starts no threads, opens no files and prints nothing.

4. Layering. The lower layers do not depend on the upper ones: `notes.errors` imports nothing from the package; `notes.store` does not import `notes.http_api`, `notes.cli` or `notes.worker`; `notes.http_api` does not import `notes.cli`.

5. Behaviour is unchanged: the command line, the HTTP API, the data file formats, the error messages and the exit statuses are exactly those of stage 7.
