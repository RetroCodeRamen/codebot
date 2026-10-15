# Notes application, stage 2: tags and search (amendment)

The notes application of stage 1 stays in force: everything it required (store, validation, ids, persistence, command line, exit statuses) must keep working exactly as before, the same tests will be run again. Change the existing application; do not start over. Python 3, standard library only.

## Requirements

1. Tags. Every note gets the key `tags`: a list of lowercase tags in the order they were added. A tag is stripped, lowercased and matches `[a-z0-9][a-z0-9_-]{0,19}`; duplicates are dropped (the first stays); at most 10 tags; anything else (also a plain string instead of a list, or a non-string element) is a `ValidationError` and changes nothing. `NoteStore.add(title, body="", tags=())` and `NoteStore.update(note_id, title=None, body=None, tags=None)` take them (`tags` given in `update` REPLACES all tags, `tags=[]` removes them all). A data file written by stage 1 (notes without `tags`) still loads, and its notes have `tags == []`.

2. Search. `NoteStore.search(query="", tags=())` returns the notes (copies, ordered by id) in which EVERY word of the query occurs, ignoring case, in the title or the body, and that carry EVERY given tag (tags are normalised like in requirement 1; invalid ones are a `ValidationError`). An empty query matches all notes; the query is split on whitespace.

3. Tag overview. `NoteStore.tags()` returns the tags in use as a list of `(tag, count)` tuples sorted by tag, where count is the number of notes that have the tag.

4. Command line. `add TITLE [--body B] [--tag T]...` and `edit N [--title T] [--body B] [--tag T]...` take tags (`edit` with `--tag` replaces all tags; `--tag` alone is enough to edit). `list [--tag T]...` lists the notes that have all the given tags. A line of a note that has tags is its stage 1 line followed by two spaces and the tags as `#tag` separated by single spaces (for example `  3  Shopping  #home #errand`); notes without tags are listed exactly as before. `search WORD... [--tag T]...` prints the matching notes in the same line format, or `No matches.`. `tags` prints one line `TAG: COUNT` per tag sorted by tag, or `No tags.`. Invalid tags are an error with exit status 2, like every validation error.
