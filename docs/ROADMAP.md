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
- **D2. Sandbox with bubblewrap (`bwrap`, already installed), not a dedicated user or Docker.** No root needed,
  no touching anything outside the codebot dir, `--unshare-net` blocks all egress for model-issued commands. The
  worker process itself (which talks to Ollama) stays outside the sandbox; only the commands it executes run inside.
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
| C | F2 git checkpoints | todo |
| D | F6 controlled replanning | todo |
| E | F3 budgets and stuck detection | todo |
| F | F5 adaptive sizing | todo |
| G | F4 escalation ladder | todo |
| H | F7+F8 native worker | todo |
| I | F9 evidence-backed memory | todo |
| J | F10 sandbox and audit | todo |
| K | F11 modes | todo |
| L | F12 morning report | todo |
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
