# codebot roadmap: a harness for small, slow, offline models

Identity: the autonomous coding agent built for small, slow, fully offline models (ornith:9b on CPU via the
private Ollama on :11500) running unattended for hours. Every feature exists to make a weak model finish real
projects without supervision.

Constraints: local only; existing commands and the codebot-ui flow keep working; extend before rewriting;
ask before anything destructive or hard to reverse.

## 0. The core shift (why the order below is what it is)

Today the *model* runs the loop. The prompt tells it to read ~6 files with tool calls (measured: ~8 min of a
45 min iteration), pick a task, edit PLAN.md to tick its own checkbox, commit, and write `DONE`. Everything on the
feature list moves that authority into the harness. So the foundation is:

- **`tasks.json`** per project: the structured task list (id, title, files hint, frozen check, status, attempts,
  size, parent, history). `PLAN.md` becomes a *rendered view* the harness regenerates. The model never edits it.
- **An engine** (`lib/codebot/`, python stdlib only, entry `bin/codebot-engine`) that owns the state machine: pick
  next task, pack context, run a *worker*, run the frozen check, checkpoint or escalate. The bash loop stays the
  supervisor (pause/resume, phone control, notifications, watchdog, budgets across steps) and calls
  `codebot-engine step <project>` once per attempt.
- **A native worker** (python, talks to Ollama `/api/chat` with a JSON-schema `format`) replacing opencode as the
  default engine for the new pipeline. See decision D1.

Existing projects are **not migrated destructively**: a project without `tasks.json` keeps running on the legacy
bash loop exactly as today. `codebot freeze <name>` creates `tasks.json` from PLAN.md additively; PLAN.md and every
other file are kept. (This is why no per-project format change breaks anything.)

## Decisions (made without asking; flagged because they could go either way)

- **D1. Native worker instead of opencode for the new pipeline.** opencode brings its own system prompt, free-form
  tool calls and no pre-apply hook, so the harness cannot (7) validate each edit before it lands, (8) guarantee the
  context contains "nothing else", or (10) audit every command/file. A ~400-line python worker with a constrained
  action schema fixes all three and cuts per-request prompt size from ~18k tokens to a few k (the measured RE run
  carried 12k tokens of unused MCP schemas). opencode stays as `engine: opencode` for modes that want MCP, so
  nothing is removed. This is a rewrite of the worker only, not of the supervisor, CLI or UI.
- **D2 (REVISED in step J). Sandbox with Docker, not bubblewrap.** Planned bwrap, but Ubuntu 24.04 here has
  `kernel.apparmor_restrict_unprivileged_userns=1`, which strips the capabilities bwrap needs (`bwrap --unshare-net` fails with
  `RTM_NEWADDR: Operation not permitted`; `unshare -Urn` fails too). Docker works for this user and is stronger anyway:
  `--network none`, read-only root, caps dropped, no-new-privileges, memory/pid limits. It uses an image that is ALREADY local
  (`ubuntu:24.04`, same distro as the host) with the host's `/usr` mounted read-only over it, so nothing is created or pulled
  in Docker and nothing outside the codebot dir is modified; each command is a throwaway `--rm` container. The worker process
  itself (which talks to Ollama) stays outside the sandbox; only the commands and checks it triggers run inside.
- **D3. Tests without the real model.** The 9B model is slow (minutes per step) and currently busy with the RE
  test. A scripted fake Ollama + scripted workers (`tests/`) exercise the harness logic in seconds. Real-model
  runs are a final smoke test per feature group, scheduled when the model is free.
- **D4. Tamper-evident, not root-append-only audit log** (no `chattr +a` without sudo): JSONL with a hash chain.

## Gap analysis

| # | Feature | Exists today | Missing |
|---|---------|--------------|---------|
| 1 | Per-task locked checks | One project-level `verify.sh`, written by the model, unprotected, checked only at DONE | per-task checks, written first, hashed and frozen, tamper detection, harness-owned completion |
| 2 | Git checkpoints | The *model* runs `git commit` itself | harness commits after each verified task, tags, reset-to-last-good on retry |
| 3 | Budgets, stuck detection | `ITER_TIMEOUT`, `MAX_ITERATIONS`, `STALL_LIMIT` (state-hash unchanged x3; fooled by trivial edits), verify fail cap 3 | per-task attempt cap, project wall-clock cap, same-failure-N, edit ping-pong, hand-off to ladder |
| 4 | Escalation ladder | A global BLOCKED that stops everything until a human answers | the ladder, per-task blocked state, run continues, larger-model hook |
| 5 | Adaptive sizing | Planner prompt says "small tasks" | auto-split on repeated failure, success-rate stats, feedback to planner |
| 6 | Controlled replanning | Model can freely rewrite PLAN.md (observed: it rewrote task text) | add/split/reorder only, never delete or weaken a check, reason log |
| 7 | Constrained outputs | None (free text STATUS, checkbox edits) | JSON-schema actions, per-edit validation, repair/retry |
| 8 | Context packing | Model reads files via tool calls; no repo map | harness-built context under a token budget |
| 9 | Evidence-backed memory | `lessons.md`, model-appended, model-condensed, size cap | append-only raw log, provenance, outcome correlation, pruning |
| 10 | Offline + audit | `loop.log`; deny rules in opencode.json; `--auto` approves external dirs; no sandbox | sandbox, audit log, `codebot audit` |
| 11 | Pluggable modes | `MODE=code|re` hard-coded in bash, mkconfig, UI, `prompts/re-*.md` | mode folders, no code change to add a mode |
| 12 | Morning report | COMPLETE/BLOCKED pushes, `status` | one summary report via notify |
| - | Grill-me planner | Built, never run live | live end-to-end test, emit checks per task |

## Implementation order and reasoning

Dependencies first. Each step commits, updates this file, and has its own test.

| Step | Feature | Why here |
|------|---------|----------|
| A | Baseline: commit current work, `tests/` harness (fake Ollama, scripted workers), `tasks.json` schema, `codebot freeze`, engine `step` skeleton | everything else needs the state machine and a way to test it fast |
| B | **F1** per-task locked checks | the foundation of "never self-certify"; F2-F6 hang off it |
| C | **F2** git checkpoints | the ladder's reset step needs a known last-good commit |
| D | **F6** controlled replanning (add/split/reorder, change log) | the split step of F4/F5 is a replanning operation; do the safe primitive once |
| E | **F3** budgets and stuck detection | produces the "stuck" signal the ladder consumes |
| F | **F5** adaptive sizing (split + stats) | needs F6 primitive and F3 failure signals |
| G | **F4** escalation ladder | uses B, C, D/F, E; includes the larger-model hook |
| H | **F7 + F8** native worker: JSON-schema actions, edit validation, context packing | biggest piece; steps B-G are testable with scripted workers, so it comes after them |
| I | **F9** evidence-backed memory | needs task outcomes (G) and the worker's lesson capture (H) |
| J | **F10** sandbox, audit log, `codebot audit` | wraps the worker's command execution (H); audit hooks are cheap to add once H exists |
| K | **F11** pluggable modes | needs the real shape of prompts/allowlist/verifier first; converts REA and code |
| L | **F12** morning report | consumes everything above |
| M | Grill-me live test + planner emits checks | needs the real model; scheduled when the RE test finishes |
| N | Real-model smoke runs, README/docs | |

The earlier efficiency review (CLI-first for RE, short env-var paths, no narration, inline context) is absorbed
by H (native worker) and K (modes); it is not applied to the legacy opencode path.

## Features: acceptance criteria and tests

### F1 Per-task acceptance checks, locked
- Planner format: each task has `check:` (a shell command run in the code dir) and optionally frozen fixture files
  (e.g. a test file) stored in `projects/<name>/checks/`. Checks exist before any code.
- `codebot freeze <name>`: parses PLAN.md into `tasks.json`, writes `checks/<id>.sh`, records sha256 of every
  check and fixture in `checks.lock`. Tasks without a check are refused by the engine (strict) unless
  `ALLOW_UNCHECKED=1`, in which case the project-level verify.sh gate is used.
- Before every check run the engine verifies hashes; a mismatch = tamper: restore from the frozen copy, log it, count
  as a failed attempt, notify. Checks live in the state dir, outside the worker's writable area.
- Done only when the check exits 0, set by the engine. The worker's claim of success is ignored.
- Tests: scripted worker that (a) fixes code -> done; (b) edits a check -> tamper detected, restored, attempt
  failed; (c) claims DONE without fixing -> not done; (d) project with no checks refuses to start strict.

### F2 Git checkpoints
- After a verified task the engine commits (`codebot: T3 <title>`) and tags `codebot/<name>/T3`.
- On retry-fresh the engine `git reset --hard` + `git clean -fd` (code dir only, state untouched) to the last tag.
- Tests: fail twice then pass; confirm the tree equals the last good commit before the fresh retry and that commits
  exist only for verified tasks.

### F6 Controlled replanning
- Engine API `plan_op`: `add_task`, `split_task`, `reorder` only. Deleting a task or changing its check is rejected.
  Every op appends `{ts, actor, op, reason, before, after}` to `plan.log`. PLAN.md is re-rendered from tasks.json.
- Tests: each allowed op works and logs; delete/weaken/edit-check rejected and logged.

### F3 Budgets and stuck detection
- Config: `TASK_MAX_ATTEMPTS`, `PROJECT_MAX_MINUTES`, `MAX_ITERATIONS`, `STUCK_SAME_FAILURE` (default 3),
  `STUCK_NO_PROGRESS` (default 4 attempts without a task completing), `PINGPONG_WINDOW`.
- Signals: same normalised check failure N times; no task completion in N attempts; the same file's content
  hash returning to a previous value (edited back and forth).
- A signal never loops: it sets the task's ladder level (F4). Wall-clock cap ends the run with a report.
- Tests: scripted workers that repeat a failure / flip a file / do nothing -> the signal fires at N.

### F5 Adaptive task sizing
- Split on repeated failure into 2-4 subtasks, each with its own frozen check (derived by the split worker or,
  without a model, a mechanical split by listed files).
- `stats.json`: attempts and success by task size bucket and by type tag; `codebot prompt` feeds it into the planner
  ("tasks over size S succeeded 30% here; cut tasks to <= S").
- Tests: a task failing K times is split; children inherit the parent's check as the final gate; stats update;
  planner prompt contains the stats line.

### F4 Escalation ladder
- Per task, in order: (1) retry with the error in context, (2) reset to last good and retry fresh, (3) split,
  (4) retry with alternate prompt/temperature, (5) mark blocked, notify, ask via `answer`.
- Blocked tasks do not stop the run; the engine moves to the next unblocked task; tasks depending on a blocked one
  wait. `codebot answer` resolves a blocked task and resets its ladder.
- Hook: `ESCALATE_MODEL` config. If set, a step between 4 and 5 re-runs the task with that model (not required to
  exist; tested with the fake server).
- Tests: scripted worker that never succeeds walks all rungs in order and ends blocked with the run continuing;
  a worker that succeeds at rung N stops there.

### F7 + F8 Native worker, constrained outputs, deterministic context
- Worker turns use Ollama `/api/chat` with `format` = JSON schema of one action: `read_file`, `write_file`,
  `edit_file` (exact search/replace), `run` (command), `note_lesson`, `finish`. Thinking off. Invalid JSON or an
  action failing validation is repaired once, then counts as a failed turn, never applied blindly.
- Edit validation before apply: path inside the code dir; target text must match exactly for `edit_file`; syntax
  check by extension (py_compile, bash -n, node --check, json.loads); refuse writes to frozen check paths.
- Context packer builds every prompt: task + check text, repo map (tree + symbols via ctags-less regex scan), the
  files the task names (or the top-ranked by name match), the last check error, relevant lessons, plan summary,
  within `CONTEXT_BUDGET` tokens (default 12000 of the 32k window). Nothing else.
- Tests (fake Ollama): malformed JSON is repaired; invalid edit rejected; syntax-broken Python write refused;
  context never exceeds budget and includes the named files and last error; a real tiny run with the live model
  at the end.

### F9 Evidence-backed memory
- `memory/lessons.raw.jsonl` append-only: `{id, text, task, project, ts}`. Each task attempt records which lesson ids
  were packed into its context and its outcome. `lessons.md` is rebuilt from raw: lessons whose packed attempts
  fail more often than baseline (min samples) are pruned; size capped.
- Tests: synthetic outcomes make a bad lesson drop out and a good one stay; cap respected; raw never rewritten.

### F10 Provably offline, auditable
- Model-issued commands run under `bwrap --unshare-net`, code dir read-write, state dir and memory read-only,
  system read-only, private /tmp. File actions are path-checked in the worker.
- `audit.jsonl` per project, hash-chained: every command (argv, cwd, exit, duration), every file write (path, sha
  before/after), every model request (endpoint, model, tokens), every check run.
- `codebot audit <project>`: verifies the chain, lists commands and changed files, and reports network: worker
  traffic only to 127.0.0.1:<ollama port>; model commands had no network namespace. States plainly that ntfy
  notifications are the one external egress and are sent by the harness (can be disabled with `NTFY_ENABLED=0`).
- Tests: a command that tries `curl`/`ping`/DNS fails inside the sandbox; chain verification detects a tampered
  line; audit output matches a scripted run.

### F11 Pluggable modes
- `modes/<name>/mode.json`: `{engine, prompts: {plan, work}, tools: [allowlist], memory: file, verifier: style}` plus the
  prompt and memory files in the folder. `modes/code` and `modes/re` replace `prompts/*` and the hard-coded MODE
  branches; unknown mode names load from the folder with no code change.
- Tests: add a throwaway mode folder (`modes/docs`) and run it with zero code edits; existing `MODE="re"` projects keep working.

### F12 Morning report
- `codebot report <name>` (and automatically at run end or on the first resume after a long idle): tasks
  done, blocked tasks with exact questions, diffs worth reviewing (largest, riskiest), lessons added/pruned, elapsed
  time and attempts. Sent through `notify`; full text saved to `projects/<name>/report-<date>.md`.
- Tests: scripted multi-task run with a blocked task produces a report containing each section.

### Grill-me live test
- Run `codebot plan` on a throwaway project with the real model; confirm it asks successive questions, writes
  GRILL.md, and (after the planner prompt update in B) emits a `check:` per task that `freeze` accepts. Fix what breaks.

## Status

| Step | Feature | Status |
|------|---------|--------|
| A | Baseline, tests harness, tasks.json, freeze, engine skeleton | **done** (9 tests) |
| B | F1 locked checks | **done** (10 tests) |
| C | F2 git checkpoints | **done** (7 tests) |
| D | F6 controlled replanning | **done** (22 tests) |
| E | F3 budgets and stuck detection | **done** (12 tests) |
| F | F5 adaptive sizing | **done** (16 tests) |
| G | F4 escalation ladder | **done** (18 tests + supervisor integration) |
| H | F7+F8 native worker | **done** (32 tests against a fake Ollama; real-model run pending) |
| I | F9 evidence-backed memory | **done** (14 tests) |
| J | F10 sandbox and audit | **done** (21 tests incl. 9 against real Docker) |
| K | F11 modes | todo |
| L | F12 morning report | **done** (12 tests + supervisor push test) |
| M | Grill-me live test | blocked on RE test finishing (model busy) |

## Notes and learnings (updated as work proceeds)

- The running RE test uses the legacy loop and opencode; none of this work touches its files.
- A: `lib/codebot/` (config, tasks, planparse, checks, engine) + `bin/codebot-engine`; `tests/run.sh` runs the suite
  in a throwaway CODEBOT_HOME in <1s. Freeze is additive: PLAN.md is kept, then re-rendered from tasks.json
  (`- [ ] T1. title` lines, so the bash counters keep working). The bash loop is not yet switched over; that happens
  once the ladder (G) defines every event it must handle. Lesson: close files in library code (ResourceWarnings
  flagged leaks immediately).
- B: tamper is a failed attempt even if the real check would pass (a worker that touches the locks gets no credit);
  everything is restored from `checks/.frozen`. `tasks.json` has a sha + known-good copy written on every harness
  save, so a worker editing it directly (mark tasks done, rewrite a check) is detected and rolled back. This is
  detection + undo; the F10 sandbox later makes the state dir read-only so the edit cannot happen at all.
- C: `gitops.py`. The first step makes a baseline commit of everything already in the code dir (reuses HEAD when the
  tree is clean), so `reset --hard` + `clean -fd` (never `-x`) cannot destroy pre-existing user files or ignored
  venvs/caches. Verified task = commit `codebot: T3 <title>` + tag `codebot/<project>/T3` + `checkpoints.json`.
  `step --fresh` resets first (the primitive the ladder uses). Not yet done: the legacy bash path still lets the
  model commit; engine projects commit only after the harness verifies.
- D: `planops.py`. Allowed ops: add_task, split_task, reorder; anything else, a missing reason, a missing check, a split of a
  done task, a dependency-breaking reorder, or a fixture that would overwrite a locked file is rejected AND logged
  (`plan.log`: ts, actor, op, reason, ok, before/after). New checks are frozen on the spot (`freeze(only_new=True)`), old ones
  are byte-identical (tested). Split: children run in order before the parent; the parent keeps its original check
  as the final gate. Two engine changes came out of it: (1) the check now runs BEFORE the worker, so an already-
  satisfied task (a split parent, or a weak check) is marked done without wasting a model call, and weak checks are
  logged; (2) silent checks (`grep -q`, `test -f`) used to hand the worker an empty error, so a failing check with no
  output is now described ("check X exited with status 1 and printed nothing").
- E: `budgets.py`. Signals: same_failure (identical normalised failure signature N times), task_budget, no_progress (N
  attempts since any task last completed), edit_ping_pong (a file's content hash returns to an earlier value;
  attempts now record `files: {path: sha}` from `git status`), plus project caps (MAX_ITERATIONS attempts,
  PROJECT_MAX_MINUTES) that return `event: budget` before any worker runs. A signal blocks the task (interim
  hand-off until the ladder, G) and the run continues with other tasks; dependents of a blocked task report
  `stalled`, a finished run with blocked tasks reports `done_with_blocked`. A bounded-termination test runs an
  always-failing worker and asserts the run ends. Failure signatures depend on check output, so a SILENT check
  (grep -q) gives every failure the same signature; the planner prompt should prefer checks that print why.
- F: `sizing.py`. Auto-split: SPLIT_CMD hook (the model-driven splitter plugs in here in H) else a mechanical splitter
  (one child per listed file with a compile/exists check, or one child per top-level `&&` conjunct of the task's
  check, quote-aware). Children get their own frozen checks, the parent keeps its original check as the gate, and
  stats record the split. `AUTO_SPLIT_AFTER=K` splits inside `step` after K failed attempts (default 0 = off; the
  ladder, G, owns the rung order). Stats: per-project `stats.json` and machine-wide `memory/sizing.json`, by size
  (S/M/L) and by type (heuristic keywords): tasks/done/first-try/attempts/split/blocked. The planner prompt gets
  `{{SIZING}}` = e.g. "S: 100% first-try ... Cut tasks to size M or smaller." (default "no history: size S").
  Planner format now requires `check:`, `files:`, `size:` per task and tells it to write silent-check-proof
  checks and fixture tests into `checks/`; this prompt change is untested with the real model (step M).
- G: `ladder.py` + the rewritten `engine.step`. Rungs per task: 1 retry with the error (2 tries) -> 2 reset to last good +
  retry fresh (2) -> 3 split (immediate) -> 4 alternate prompt/temperature (2; env CODEBOT_VARIANT=alt,
  CODEBOT_TEMPERATURE) -> 5 larger local model (only if ESCALATE_MODEL is set; env CODEBOT_MODEL) -> 6 blocked.
  LADDER_TRIES overrides. A stuck signal (E) skips ahead one rung instead of blocking; only the hard attempt cap
  blocks outright. Unavailable rungs (no split possible, no big model) are skipped and logged. Blocked tasks ask a
  specific question (task, title, last error, the exact `codebot answer` command) and the run moves on; dependents
  report `stalled`. `answer` (CLI or phone) returns the task to rung 1 with a fresh attempt budget (`attempts_base`)
  and "T2: ..." prefixes pick the task. A step killed by pause/crash is refunded, not charged.
  Supervisor: `loop_engine` in bin/codebot (chosen when tasks.json exists; legacy projects untouched). It handles
  pause/phone, pushes (blocked = urgent, tamper, split, budget, COMPLETE with verified counts), idles waiting for an
  answer instead of exiting, and runs the legacy project-level verify.sh as a final gate (failure becomes a frozen
  repair task, max 3). `codebot start` auto-freezes a PLAN.md that carries `check:` lines. The integration test runs
  the real bash loop against a fake ntfy server. Replaced the interim "stuck -> block" from E and the AUTO_SPLIT_AFTER
  option from F (the ladder owns that now).
- Operational finding (user hit it, PC overheating): `codebot pause` did not kill the in-flight iteration of a loop that
  had been started via systemd-run, because the agent was not in the process group the pause signalled, so the model kept
  generating at ~380% CPU. Pause/stop now terminate the whole process TREE (`kill_tree`) as well as the group. Also: a paused
  project must mean zero CPU; verify with `ollama ps` and `pgrep llama-server` after pausing.
- H: `ollama.py`, `packer.py`, `worker.py`, `sandbox.py` (policy only; bwrap comes in J), `splitter.py`, `tests/fake_ollama.py`.
  Each turn is ONE action constrained by a JSON schema (`anyOf` of per-action objects; if the server rejects it
  with HTTP 400 the worker falls back to a flat schema), thinking off. Validation before anything lands: path
  must stay inside the code dir (realpath, symlinks, .git, the state dir are refused), writes/edits are syntax-checked
  (py compile, json, bash -n, node --check) and applied atomically, `edit_file` needs exactly one match, malformed
  or unknown actions are bounced back with the reason (3 strikes ends the attempt), 3 repeats of the same action warn
  and 5 abort. `run` goes through a denylist (sudo, network tools, history-rewriting git, package installs, the
  harness state dir). The packer builds the whole prompt: task, the frozen check script + fixtures, last check
  output, human answers, GRILL.md decisions, goal, previous attempts, lessons, a repo+symbol map, and the files the
  task names or the check/error mention, inside CONTEXT_BUDGET (default 8k of the 32k window); history is compacted
  when the conversation outgrows WORKER_CONTEXT_TOKENS. `replan` and `note_lesson` are actions (replan goes through
  planops; lessons go to the raw log for F9). Model-driven splitter plugs into sizing with mechanical fallback.
  NOT yet verified: the real ornith:9b obeying the anyOf grammar and actually solving a task. That is the smoke test
  (step N) and needs the machine to be cool. Known limit until J: `run` is policy-checked, not isolated (a clever
  command could still read outside the project); bwrap closes that.
- I: `memory.py`. Source of truth is `memory/lessons.raw.jsonl` (append-only; test asserts earlier bytes never change;
  duplicates are skipped but their provenance is kept). `lessons.status.json` holds, per lesson, which project/task
  produced it, how many attempts used it, how many passed, and a `pruned` flag with the reason; plus a baseline of
  all attempts. The packer selects non-pruned lessons by relevance and the engine reads `packed.json` (removed before each
  attempt so it cannot be stale) to attribute every attempt's outcome to the lessons actually in its context. Pruning is
  deliberately conservative (>= 8 uses, >= 20 baseline attempts, pass rate more than 15 points under baseline) because
  correlation is not causation; pruned lessons stay in raw. `lessons.md` is generated best-evidence-first under
  LESSONS_MAX, so the model can no longer corrupt it; `codebot tidy` now needs no model when raw exists.
- J: `sandbox.py` (policy + Docker execution), `audit.py` (hash-chained log, verify, report/render). Model-issued commands AND the
  frozen acceptance checks run in `docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges
  --user <you> --pids-limit --memory` with only the code dir writable and the frozen checks mounted read-only; home, the harness
  state dir and the network are simply absent (tested: DNS and raw sockets fail, `ls ~` fails, `/usr` `/etc` read-only, CapEff=0,
  timeouts kill the container and none are left behind). A prototype caught `--init` hiding behind the `/usr` mount, so it is
  not used. The audit log records every command (and every refused one), file read/write with before/after sha, model request
  (endpoint, tokens), and check run, each entry hashing the previous; edits, mid-file deletions and (while `audit.head` is
  intact) tail cuts are detected. `codebot audit <project> [--probe]` prints sandbox/chain/commands/files/model/egress and a
  verdict, exits non-zero on any warning (unsandboxed command, broken chain, loopback-leaving traffic, probe reaching the net).
  The probe tries raw sockets and DNS from inside the sandbox. The harness's own egress (ntfy notifications and the phone-reply
  poll) is logged to `logs/egress.jsonl`; `NTFY_ENABLED=0` / `PHONE_CONTROL=0` make it zero (tested: not a single request).
  Honest limits: the docker group is root-equivalent on the host (it is the harness, not the model, that holds it); the worker
  process and engine are trusted code outside the sandbox; `audit.head` is not root-protected (D4); SANDBOX=auto falls back
  to unsandboxed if Docker or the image is missing, but the audit then says so and exits non-zero.
- K: `modes.py` + `modes/<name>/`. A mode folder = `mode.json` (engine, extends, prompts / prompts_add, opencode config+agent+build, verifier
  helpers + guidance, command allowlist + deny regexes, sandbox mounts/env/PATH/memory, worker_rules, memory_file, wants_target) plus
  the files it names. `extends` merges (lists like deny/ro/env concatenate, other keys override; prompts = nearest full prompt + every
  `prompts_add`). The old `prompts/` and `memory/rea.md` moved into `modes/code` and `modes/re` with `git mv`; the hard-coded MODE=re
  branches in bash (oc_setup, render_prompt, mkconfig use) now read the mode: `codebot-engine mode-info|prompt-template|modes`.
  What a mode controls at runtime: the planner and legacy-worker prompts; extra rules appended to the native worker's system prompt;
  a notes file packed into context (MODE NOTES, capped); verifier helper scripts copied into the project's frozen `checks/` at freeze;
  which shell commands `run` accepts (allowlist on the first word of every pipeline segment, no command substitution when an allowlist
  exists, plus deny regexes); extra read-only sandbox mounts, env passthrough, PATH prefix and memory. REA is now data: `re` extends `code`,
  allows `rea`/grep/jq/... and denies `rea capture-*|observe-*|setup|update|uninstall`, adb, gdb, lldb, strace; mounts node, the JDK and
  Ghidra read-only; `codebot plan --mode re --target <path>` mounts the target read-only (stored as SANDBOX_RO). Verified by hand that
  `rea search` + Ghidra run inside the offline container (18 s on a copy of /bin/ls; the package reached 84 C, so not repeated).
  Test of the requirement: a throwaway `docs` mode created in a temp home with only files (no code edits) loads, renders its prompts,
  freezes and locks its verifier helper, constrains commands, injects its rules and notes, and shows up in `codebot modes`.
  Lessons: (1) a child mode must inherit `engine`, so required fields are validated after merging; (2) freeze must tolerate a home without
  modes/ (bare homes, old installs) but not an unknown mode name; (3) moving files broke 100+ older tests at once, which is what the suite
  is for. `codebot-ui` now lists modes from the folder and asks for target paths when the mode wants them.
  Not done: the legacy opencode path still builds its REA MCP config with `bin/codebot-mkconfig` (named in re/mode.json, so it is data, but
  the builder itself is REA-specific); a new MCP-based mode would need its own builder script.
- L: `report.py`. Built only from the harness's own records: tasks.json (done / blocked with the exact question / waiting on which
  blocker), attempts.jsonl (attempts, rungs, tamper and interrupted events, check + worker time; `worker_secs` is now recorded),
  plan.log (escalations, model replans accepted vs refused and why, weak checks), checkpoints + git (per-task commits scored for review:
  files outside the task's `files:` hint, build/config/script files, mostly-deletion and large diffs, binary/mode-only changes), the
  lessons store (added / pruned since the run started), and the audit report (chain, sandbox, warnings). `codebot report <name>` prints
  markdown (`--short`, `--json`, `--send`); `--write` saves `projects/<name>/report-<date>-<HHMM>.md`. The supervisor sends it once when
  the run first goes idle waiting on you, and again at COMPLETE or a budget stop, via the existing notify path (so NTFY_ENABLED=0 silences
  it and the file is still written). The push text leads with `N/M tasks verified`, then blocked questions, the first thing to review,
  and the safety line, capped at 1200 chars. Legacy (non-frozen) projects return a clear message instead of a report.
