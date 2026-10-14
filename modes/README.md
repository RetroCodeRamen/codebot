# Modes
A mode is a folder here. Adding one needs no code change: create `modes/<name>/mode.json` plus the files it names. List them with `codebot modes`;
use one with `codebot plan <project> <dir> --mode <name>` (stored as `MODE="<name>"` in the project's `project.env`). `extends` inherits another mode.
Built in: `code` (default) and `re` (reverse engineering with REA, extends `code`). `tests/test_k_modes.py` creates a throwaway mode with only files and checks it works.

`mode.json` fields (merged over the base when `extends` is set; lists such as `deny`, `ro`, `env`, `path_prepend`, `helpers`, `build_deps`, `requires` are concatenated, everything else overrides):

| field | meaning |
|---|---|
| `name` (required), `description`, `extends` | identity; `name` must equal the folder name |
| `engine` | `native` (the built-in constrained-output worker) or `opencode` (the legacy opencode loop). Required unless inherited |
| `prompts` | `{plan, plan_quick, work}` file names in this folder (the nearest mode that defines one wins) |
| `prompts_add` | `{plan, work}` addendum files appended after the base prompt (what `re` uses) |
| `worker_rules` | file appended to the native worker's system prompt (base rules first, then each mode's) |
| `memory_file`, `memory_cap` | notes file packed into the worker's context as "MODE NOTES" (capped, characters) |
| `verifier` | `{style, helpers, guidance}`: scripts in `verifiers/` named in `helpers` are copied into the project's frozen `checks/` at freeze, so checks can call them via `$CODEBOT_CHECKS/<name>` |
| `commands` | `{allow, deny}` for the worker's `run` action: `allow` is an allowlist of command words checked on every pipeline segment (`null` = any; command substitution is refused when an allowlist exists); `deny` is a list of regexes |
| `sandbox` | `{ro, env, path_prepend, memory}`: extra read-only mounts, env passthrough, PATH prefix and memory cap inside the Docker sandbox. `{NODE_PREFIX}`, `{JAVA_HOME}`, `{GHIDRA_INSTALL_DIR}`, `{HOME}` expand to this machine's paths; missing ones are dropped |
| `opencode` | `{config, agent, build, build_deps, requires}` for the legacy loop and the interactive planner: config file, agent name, a script that builds the config, files that trigger a rebuild, commands that must exist on PATH |
| `wants_target` | `true` makes the menu ask for the path(s) to analyse (mounted read-only via `SANDBOX_RO`) |

Prompt files use these placeholders: `{{STATE}}`, `{{WORKDIR}}`, `{{NAME}}`, `{{CODEBOT_HOME}}`, `{{MODE_DIR}}`, `{{SIZING}}`. `plan-auto.md` and `plan-repair.md` (in `code`) are used by
`codebot bench --plan auto`, which drives the planner unattended in rounds and runs `check-plan` itself.
