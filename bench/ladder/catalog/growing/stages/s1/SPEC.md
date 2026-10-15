# Notes application, stage 1: notes you can create and store

This is the first of eight stages in which ONE notes application grows. Later stages will extend the code you write now, so keep it clean. Python 3, standard library only, no network. Everything lives in the code directory; the first version is the single file `notes.py` (run as `python3 -m notes`).

## Requirements

1. `notes.py` defines `NoteStore(path, clock=None)`, a store of notes kept in the file `path`. `clock` is a function returning the current time as `YYYY-MM-DDTHH:MM:SS`; by default the environment variable `NOTES_NOW` if it is set, otherwise the local time. A note is a dict with the keys `id` (int), `title`, `body`, `created` and `updated` (timestamps from the clock). Methods: `add(title, body="")` returns the new note; `get(note_id)`; `list()` returns all notes ordered by id; `update(note_id, title=None, body=None)` changes only the given fields, sets `updated` and returns the note; `delete(note_id)` removes and returns the note. All of them return copies: changing a returned dict never changes the store.

2. Ids are integers starting at 1 and are never reused, also after deletions and after reopening the file. `created` is set once; `updated` equals `created` until the first update.

3. Validation: a title is a string that is not empty after stripping, is at most 100 characters and has no line break; the stored title is the stripped one. A body is a string of at most 5000 characters (it may be empty and is stored as given). `ValidationError` (a subclass of `NotesError` and of `ValueError`) is raised for anything else and nothing is changed. A missing id raises `NotFound` (a subclass of `NotesError` and `KeyError`, with the id as `args[0]`).

4. Persistence: every change is stored in the file at once, atomically (a crash can never leave a half-written file and no temporary files remain), and a new `NoteStore` on the same path sees exactly the same notes and the same next id. A missing file is an empty store. A file that cannot be read as a notes file raises `StoreError` (a subclass of `NotesError`) with the message `data file is corrupt: PATH` and is left untouched.

5. Command line, `python3 -m notes COMMAND`, with the file named by the environment variable `NOTES_FILE` (default `notes.json` in the current directory). `add TITLE [--body B]` prints `Added note N`. `list` prints one line per note, `f"{id:>3}  {title}"` (id right-aligned in 3 columns, two spaces, the title), or `No notes.`. `show N` prints the title on the first line and the body after it. `delete N` prints `Deleted note N`. `edit N [--title T] [--body B]` prints `Updated note N` (without an option it fails with the message `nothing to edit`). `notes.py` also provides `main(argv=None, out=None)`, which returns the exit status instead of exiting.

6. Exit statuses and errors: 0 on success; 1 when the note does not exist (`Error: no note with id N` on stderr); 2 for everything else (`Error: MESSAGE` on stderr for validation and file errors; usage on stderr for a missing or unknown command or bad arguments, such as a non-integer id).
