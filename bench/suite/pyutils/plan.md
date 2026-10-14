# Goal
Add small text and statistics utilities, a CLI over them, and a rename refactor.
# Context
Python 3, standard library only. Existing files: stats.py (has a bug), legacy.py, main_app.py. Keep the existing style.
# Definition of done
All five tasks verified by their own checks.
# Tasks
- [ ] 1. Add slugify(text) to textutil.py (lowercase; runs of non-alphanumerics become one hyphen; no leading/trailing hyphen)
  - check: `python3 $CODEBOT_CHECKS/t_slugify.py`
  - files: textutil.py
  - size: S
- [ ] 2. Add parse_kv(line) to textutil.py ("a=1;b=two;c=3.5" -> {"a": 1, "b": "two", "c": 3.5}; ignore blanks and spaces)
  - check: `python3 $CODEBOT_CHECKS/t_parse_kv.py`
  - files: textutil.py
  - size: S
- [ ] 3. In stats.py make mean([]) return 0.0 and add median(xs) (empty -> 0.0)
  - check: `python3 $CODEBOT_CHECKS/t_stats.py`
  - files: stats.py
  - size: M
- [ ] 4. Create cli.py (argparse) with `slug` and `kv` subcommands that use textutil.py (kv prints JSON)
  - check: `bash $CODEBOT_CHECKS/t_cli.sh`
  - files: cli.py
  - size: M
  - deps: 1, 2
- [ ] 5. Rename old_name to double in legacy.py and main_app.py
  - check: `bash $CODEBOT_CHECKS/t_rename.sh`
  - files: legacy.py, main_app.py
  - size: S
