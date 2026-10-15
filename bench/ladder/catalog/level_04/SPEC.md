# Persistent command-line task manager

Build `todo.py` in the root of the code directory: a task manager used as `python3 todo.py <command> ...`. Python 3, standard library only, no network.
Tasks persist in a JSON file between runs. Every command prints its normal output to stdout and its errors to stderr (`Error: ...`). Exit status: `0` success, `1` the named task does not exist, `2` any invalid input (unknown command, bad option or value, corrupt data file).
For testing, two environment variables are honoured: `TODO_FILE` is the path of the data file (default `tasks.json` in the current directory) and `TODO_TODAY` (`YYYY-MM-DD`) replaces today's date when set.

## Requirements

1. `add TITLE [--priority high|medium|low] [--due YYYY-MM-DD] [--tag TAG]...` creates a task and prints `Added task N: TITLE`. Ids are integers starting at 1 and are never reused, even after a task is deleted. The title is trimmed and must not be empty; the default priority is `medium`; `--due` must be a real calendar date (`2024-02-30` is invalid); `--tag` may be repeated, tags are stored in lowercase, and a repeated tag is stored once. An invalid priority prints `Error: invalid priority 'x'`, an invalid date prints `Error: invalid due date 'x'`, an empty title prints `Error: title must not be empty`; all exit with status 2 and change nothing.

2. `list [--all | --done] [--tag TAG] [--priority P]` prints one line per task: the id right-aligned in 3 columns, a space, `[x]` if done or `[ ]` if not, a space, the priority left-aligned in 6 columns, a space, the title, then ` (due YYYY-MM-DD)` if the task has a due date, then ` #tag` for every tag in the order they were added. For example `  1 [ ] high   Pay rent (due 2024-06-01) #money` and `  2 [x] medium Buy milk`. Without options only pending tasks are listed; `--all` lists every task; `--done` lists only completed tasks; giving both `--all` and `--done` is an error (status 2). `--tag` (compared in lowercase) and `--priority` filter further and combine with AND. If nothing matches, print `No tasks.`. Tasks appear in id order.

3. `list --sort KEY` orders the output: `id` (default), `priority` (high, then medium, then low; ties by id) or `due` (earliest due date first, tasks without a due date last; ties by id). An unknown key is an error with status 2 (`Error: invalid sort key 'x'`).

4. `done ID [ID...]` marks tasks as completed and prints `Completed task N` for each; a task that is already done prints `Task N is already done` and is not an error. `reopen ID [ID...]` marks tasks as pending again and prints `Reopened task N`. If any id does not exist, print `Error: no task with id N` for it, exit with status 1, and change nothing at all (all-or-nothing). An id that is not an integer is `Error: invalid task id 'x'` with status 2.

5. `delete ID [ID...]` removes tasks and prints `Deleted task N` for each, with the same all-or-nothing rule, error messages and statuses as `done`. `clear-done` removes every completed task and prints `Removed N completed task(s)` (`N` may be 0). Ids of removed tasks are never given out again.

6. `edit ID [--title T] [--priority P] [--due D|none] [--add-tag T]... [--remove-tag T]...` changes the given fields and prints `Updated task N`. `--due none` removes the due date. Tags removed that are not present are ignored; adding applies the same rules as in `add`. Without any option the result is `Error: nothing to edit` (status 2); an unknown id is status 1; invalid values are status 2 and nothing is changed. Marking done or pending is not part of `edit`.

7. `stats` prints exactly four lines: `Total: N`, `Done: N`, `Pending: N` and `Overdue: N`, where overdue means pending with a due date strictly before today.

8. Persistence. The data file is a JSON object `{"next_id": N, "tasks": [...]}`; each task is `{"id": int, "title": str, "done": bool, "priority": str, "due": str or null, "tags": [str]}`, and `next_id` is the id the next added task will get. A missing file means no tasks; it is created by the first command that changes something and never by commands that only read. Writes must be atomic (the file is always either the old or the new complete JSON, never a partial one). A data file that is not valid JSON, or does not have this structure, makes every command fail with `Error: data file is corrupt` and status 2, without modifying the file. Running without a command, with `--help`, or with an unknown command prints usage to stderr and exits with status 2 (`--help` exits 0 and prints to stdout).
