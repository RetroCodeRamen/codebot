# codebot memory
Shared by every project; the model only sees a few relevant lines per task (the packer selects them), so keep it short.
- `expertise.md`: yours. Preferences and conventions ("run the project's tests before declaring done"). Edit freely.
- `lessons.raw.jsonl`: append-only record of every lesson the worker saved with the `note_lesson` action, never rewritten.
- `lessons.status.json`: per lesson: which project/task produced it, how many attempts used it and how many passed, whether it was pruned and why;
  plus a baseline of all attempts.
- `lessons.md`: GENERATED from the raw log (best evidence first, under `LESSONS_MAX`). Do not edit; `codebot tidy` prunes lessons whose attempts pass
  clearly less often than the baseline and regenerates it.
- `sizing.json`: machine-wide first-try success by task size and type; fed to the planner as the sizing history.
