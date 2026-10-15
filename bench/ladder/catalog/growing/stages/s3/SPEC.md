# Notes application, stage 3: SQLite storage (amendment)

Everything required in stages 1 and 2 stays in force and its tests run again. In this stage the storage changes from a JSON file to an SQLite database in the same file (`NOTES_FILE`, `NoteStore(path)`). Change the existing application; the public interface of `NoteStore`, the command line, the output formats and the error behaviour must not change. Python 3, standard library only (`sqlite3`).

## Requirements

1. SQLite storage. `NoteStore(path)` keeps its notes in an SQLite database in the file `path` (foreign keys on, writers wait up to 30 seconds), with a meta table that holds the schema version: `NoteStore.schema_version()` returns `1`. Every operation is atomic (a failing operation leaves the database unchanged) and the store may be used from several threads and by several `NoteStore` objects or processes at once without losing updates; ids are never reused (also after deleting the newest note and reopening). No file other than the database may remain next to it after an operation (no leftover temporary or journal files).

2. Migration. If `path` holds a stage 1 or stage 2 JSON store, opening it converts it in place to SQLite: all notes keep their ids, titles, bodies, tags, timestamps and the next id continues where the JSON file left off (also when the newest notes had been deleted), the original JSON file is kept next to it as `PATH.json.bak`, and the conversion is atomic (a crash leaves either the old JSON file or the complete new database). Opening a SQLite file again converts nothing and creates no backup. A file that is neither (garbage, an empty file, JSON that is not a notes store, an SQLite database that is not a notes database) raises `StoreError` with the message `data file is corrupt: PATH` and is not modified. A missing path creates a new empty database (also when several processes create it at the same time: nobody may ever see or choke on a half-made file).

3. Export. `NoteStore.export_json(path)` writes all notes in the stage 2 JSON file format (`{"next_id": N, "notes": [...]}` where each note has its `tags`) atomically and returns the number of notes; a `NoteStore` can open such a file again (migrating it).

4. Command line. A new command `stats` prints `Notes: N` and `Tags: M` (the number of notes and of distinct tags), each on its own line. All other commands behave exactly as before.
