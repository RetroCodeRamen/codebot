# codebot

An autonomous coding agent for **small, slow, fully offline models**. It runs a local 9B model (Ornith 1.0 through a private Ollama) unattended
for hours on a laptop, and judges every task with an acceptance check the model cannot change.

Most agent harnesses assume a frontier model and fall apart on a 9B one. codebot moves the decisions out of the model: the harness plans the work
in small tasks, packs the context, validates every edit, runs every command in an offline Docker sandbox, checkpoints in git, re-checks finished
work for regressions, escalates when a task is stuck, and writes you a report in the morning. The model only fills in structured JSON.

**Status: research project, Linux only, honest numbers below.** It works end to end and is tested (437 tests), but it is slow by design and its
success rate is limited by how good a 9B model is at writing tests for its own work. Nothing leaves your machine unless you turn notifications on.

## What it can do today

Measured on one laptop (i7-11375H, 15 GB RAM, RTX 3050 Ti 4 GB) with the Capability Ladder Benchmark (`docs/BENCHMARK.md`): hidden tests the
agent never sees, 90 minutes per task, no limit on the run.

- Level 1 (8 utility functions, 64 hidden tests), 3 independent trials: **78%, 47%, 73%** of the hidden tests passed (median 73%; the bar for
  "level held" is 80%). Every function was solved in at least one trial; no task needed more than 48 minutes.
- What held it back: the model writing impossible or weak acceptance tests for itself, and later tasks breaking finished ones. Both are now
  addressed in the harness (per-item planning, assertion repair and salvage, a regression gate, `docs/dev/ROADMAP.md`); a full re-run is pending.
- Which local model to use, and whether a bigger planner plus a faster worker beats one model: `docs/MODEL_EVAL.md` (in progress).

## Requirements

- Linux on x86_64 (arm64 should work, untested). Ubuntu 24.04 is what it is developed on.
- 15 GB RAM recommended, about 15 GB free disk (the model is 5.6 GB). A GPU is optional: with an NVIDIA driver Ollama offloads part of the
  model (about 35% on a 4 GB card, about 9 tokens/s); without one it runs on the CPU, slower.
- python3 3.10+, git, curl, jq, zstd, and Docker usable by your user (every command the model runs is sandboxed in a container).
- Optional: Node/npm for opencode (only for the `codebot plan` interview planner), systemd for the watchdog.

## Install

```
git clone <this repo> codebot && cd codebot
./install.sh          # pinned private Ollama in tools/, the model in models/, your settings in config/local.env; asks before big downloads
codebot doctor        # (or bin/codebot doctor) checks everything and says how to fix what is missing
tests/run.sh          # the harness tests against a fake model, about 6 minutes, no model needed
```

Everything lives inside the folder: a private Ollama on port 11500 (it never touches a system Ollama), models in `models/`, projects in `projects/`.
The installer never uses sudo; it asks before adding a `codebot` symlink to `~/.local/bin` or a user service.

## Quick start

```
codebot                                  # the guided menu: pick or create a project, plan, start, watch live
codebot plan myproj ~/code/repo          # interactive "grill me" planning chat -> PLAN.md with a check per task (needs opencode)
codebot check-plan myproj                # test every check against today's code (rejects ones that already pass or are broken)
codebot start myproj                     # freezes the checks and starts the unattended loop in the background
codebot status | log myproj | pause myproj | resume myproj | stop myproj
codebot answer myproj "T3: use the v2 API"   # reply when a task is blocked
codebot report myproj                    # the morning report
codebot audit myproj --probe             # proof of what ran, and that it was offline
codebot diff myproj                      # review what it changed and how to merge it
codebot modes | tidy | help              # list modes / prune bad lessons / every command
codebot list | setup | notify-test       # all projects / start the private Ollama / send a test push
codebot install-service | resume-all     # watchdog timer (restarts loops that die) / what the timer runs
codebot doctor                           # is this machine ready?
codebot re-config                        # rebuild the REA agent config after editing config/rea-tools.txt
codebot bench --preflight                # prove the context setup against the live model server
codebot bench run --level 1              # the Capability Ladder Benchmark (docs/BENCHMARK.md)
codebot bench --thermal --minutes 4      # sweep threads / power profile / GPU for sustained speed vs heat
codebot eval gate MODEL                  # model evaluation: speed/compatibility gate, mutant sets, scored planner probes (docs/MODEL_EVAL.md)
```

Your code is never edited in place: for an existing git repo codebot works in a separate worktree (`workspaces/<project>/`, branch
`codebot/<project>`), and `codebot diff` shows how to merge the result. The unattended planner (spec in, plan out, no interview) is currently
used by the benchmark; a `codebot plan-auto` command for your own projects is planned.

## How a run works

1. **Plan.** `codebot plan` is a "grill me" interview (one question at a time, recommended answers, it challenges vague ones) that writes `PLAN.md`:
   small tasks, each with a `check:`, `files:` and `size:`. The unattended planner instead asks the model for one task per numbered spec item, with
   its acceptance test written as data (assertions such as `slugify("Hello World") == "hello-world"`), never as a shell command.
2. **Freeze.** `codebot start` turns the plan into `tasks.json` and **locks every check** (sha256 + a frozen copy). First the **baseline guard**
   runs each check against the untouched code: a check that already passes tests nothing and a broken one tests nothing; both are rejected. After
   freezing only the harness changes task state; a worker that edits a check, a fixture or `tasks.json` is detected, undone, and its attempt fails.
3. **Attempt.** For the next ready task the native worker runs: the model replies with one JSON action per turn under a JSON schema (`read_file`,
   `list_dir`, `edit_file`, `write_file`, `run`, `replan`, `note_lesson`, `finish`). Every write is path-checked, syntax-checked and applied
   atomically. The harness builds the whole context (task, frozen check, last error, answers, repo map, relevant files, lessons) inside a token budget.
4. **Judge.** The frozen check decides, not the model, and every task already done must still pass (the regression gate). Pass: a git checkpoint.
   Fail: the **escalation ladder**.
5. **Escalate.** Retry with the error, reset to the last good commit and retry fresh, split the task into smaller ones with their own checks, an
   alternate prompt, an optional larger model (`ESCALATE_MODEL`), then **blocked**: the exact question is saved (and pushed, if you enabled
   notifications) and the run moves on. `codebot answer` puts the task back on the ladder.
6. **Budgets.** Every task has its own wall-clock budget; stuck signals (same failure again, no progress, a file edited back and forth) skip rungs.
   A run always terminates.
7. **Report.** A morning report of what was done, what is blocked and why, and what to review.

## Safety and privacy

- Every command the model runs and every acceptance check runs in a throwaway Docker container: `--network none`, read-only root, all capabilities
  dropped, no-new-privileges, memory and pid limits, only the code dir writable, your home and the harness state invisible. The image
  (`ubuntu:24.04`) must already be local; codebot never pulls or builds images by itself.
- `audit.jsonl` is a hash-chained log of every command, file write, model request and check run. `codebot audit <project> --probe` verifies it and
  tries to reach the internet from inside the sandbox to show that it fails.
- Notifications (ntfy) are **off by default**. If you turn them on in `config/local.env`, treat the topic name like a password: anyone who knows
  it can read your pushes, and with `PHONE_CONTROL=1` pause or stop your runs and answer blocked questions, which the model then reads.
- Honest limits: the `docker` group is root-equivalent (the harness holds it, the model does not); the engine and worker processes are trusted code
  outside the container; the audit log is tamper-evident, not tamper-proof. More in `SECURITY.md`.

## Configuration

`config/codebot.env` holds the defaults, every key documented inline. Put your own settings in `config/local.env` (created by the installer, not
tracked by git); it is read after `codebot.env`, so it wins. Any key can also be overridden per project in `projects/<name>/project.env`.

Model/server: `CODEBOT_MODEL`, `OLLAMA_PORT`, `NUM_CTX` (must equal the `num_ctx` in `config/Modelfile` and the opencode configs; a test enforces
it), `KEEP_ALIVE`, `THINK`. Planning: `PLAN_STYLE=grill|quick`, `CHECK_BASELINE_GUARD`, `REGRESSION_GATE` (a task is done only when every done task
still passes), `PLAN_PER_ITEM` (one planner call per numbered spec item). Per-role models: `PLANNER_MODEL`, `PLANNER_THINK`, `SPLITTER_MODEL`,
`SPLITTER_THINK`, `WORKER_THINK`, `THINK_BUDGET` (`docs/MODEL_EVAL.md`). Worker: `WORKER=native`, `CONTEXT_BUDGET`, `WORKER_MAX_TURNS`,
`WORKER_CONTEXT_TOKENS`, `WORKER_TEMPERATURE`, `RUN_TIMEOUT`. Ladder/budgets: `LADDER_TRIES`, `ALT_TEMPERATURE`, `ESCALATE_MODEL`,
`TASK_MAX_ATTEMPTS`, `STUCK_SAME_FAILURE`, `STUCK_NO_PROGRESS`, `PINGPONG_WINDOW`, `MAX_ITERATIONS`, `PROJECT_MAX_MINUTES`. Notifications:
`NTFY_ENABLED`, `NTFY_SERVER`, `NTFY_TOPIC`, `NTFY_TOKEN` (protected topics), `PHONE_CONTROL`, `NOTIFY_EACH_TASK`. Safety: `SANDBOX=auto|docker|none`,
`SANDBOX_IMAGE`, `SANDBOX_MEMORY`, `SANDBOX_RO`, `SANDBOX_PIDS`. Heat: `THERMAL_GUARD`, `THERMAL_MAX`, `THERMAL_RESUME`, `THERMAL_MAX_WAIT`. Legacy
opencode loop only: `ITER_TIMEOUT`, `STALL_LIMIT`, `LESSONS_MAX`. Less common: `CHECK_TIMEOUT`, `WORKER_MAX_TOKENS`, `WORKER_CMD` (replace the native
worker, used by tests), `IDLE_POLL`.

## Heat and speed

A laptop running a 9B model for hours gets hot (85-96 C here). The thermal guard pauses model calls at `THERMAL_MAX` (92 C) and resumes at
`THERMAL_RESUME`; one model job runs at a time; `codebot pause` unloads the model. `codebot bench --thermal` measures thread counts, power profiles
and GPU offload for sustained speed against heat on your machine. A cooling pad helps more than any setting.

## Modes

A mode is a folder under `modes/` (`mode.json`, prompts, worker rules, verifier helpers); adding one needs no code change (`modes/README.md`).
- `code` (default): general coding with test-style checks.
- `re`: reverse engineering with [REA](https://github.com/morluto/rea): static analysis only, inside the offline sandbox, evidence-only findings.
  Needs the `rea` CLI, a JDK and Ghidra (set `SANDBOX_RO` in `config/local.env`).

## Memory

The worker can save short lessons (`note_lesson`); `memory/` keeps them with evidence: every attempt records which lessons were in its context and
whether it passed, and lessons that correlate with failure are pruned (`codebot tidy`). `memory/expertise.md` is yours to edit. Details in
`memory/README.md`. Benchmark trials get a private memory (`MEMORY_DIR`) so they never change yours.

## Documentation

| file | what |
|---|---|
| `docs/BENCHMARK.md` | the Capability Ladder Benchmark: levels, the time rule, metrics, results |
| `docs/MODEL_EVAL.md` | choosing local models: planner, worker and end-to-end scores, candidates, commands |
| `modes/README.md` | the mode format |
| `memory/README.md` | the lesson store |
| `docs/dev/HANDOFF.md` | maintainer notes: current state, file map, gotchas (written to let a person or an AI agent pick the work up cold) |
| `docs/dev/ROADMAP.md` | the design record and every real-model result, including the failures |
| `CONTRIBUTING.md`, `SECURITY.md` | how to contribute; the threat model |

## Tests

`tests/run.sh [file]` runs the suite (about 6 minutes, little CPU): a scripted fake Ollama and scripted workers exercise the harness (locking and
tamper detection, checkpoints, replanning, budgets, the ladder, worker validation, context packing, memory, the sandbox against real Docker, the audit
chain, modes, reports, the guard, the benchmark and the bash supervisor). `tests/test_r_docs.py` fails when these docs drift from the code;
`tests/test_w_corpus.py` replays every real assertion a model has written against the harness's repair and validation rules.

## License

MIT (`LICENSE`). Models are downloaded from their publishers and keep their own licences (Ornith 1.0: MIT).
