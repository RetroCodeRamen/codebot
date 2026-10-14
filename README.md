# codebot

The autonomous coding agent built for **small, slow, fully offline models** (ornith:9b on CPU through a private Ollama) running
unattended for hours. Most harnesses assume a frontier model and fall apart on a 9B one. codebot takes the decisions away from the
model: the harness plans the work, packs the context, validates every edit, judges every task with a check the model cannot
change, checkpoints in git, escalates when stuck, and tells you in the morning what happened.

Nothing leaves this machine except the ntfy notifications you choose to keep (and `NTFY_ENABLED=0` removes those too).

## Quick start

```
codebot                       # the guided menu: pick or create a project, plan, start, watch live
codebot plan myproj ~/code/repo          # interactive "grill me" planning chat -> PLAN.md with a check per task
codebot start myproj                     # freezes the checks and starts the unattended loop
codebot status | log myproj | pause myproj | resume myproj
codebot answer myproj "T3: use the v2 API"   # reply when a task is blocked (or answer from your phone)
codebot report myproj                    # the morning report
codebot audit myproj --probe             # proof of what ran and that it was offline
codebot diff myproj                      # review what it changed and how to merge it
```

Subscribe to your ntfy topic (`config/codebot.env`) on your phone: pushes for start, blocked (with the exact question), tamper,
budget stops, COMPLETE and the morning report. Buttons: Pause / Resume / Status. Reply topic: `<topic>-reply`.

## How a run works (engine mode)

1. **Plan.** `codebot plan` is a "grill me" interview (13-area checklist, one question at a time, recommended answers, it challenges
   vague answers) and writes `PLAN.md`: small tasks, each with a `check:` (a shell command), `files:` and `size:`. Decisions go to
   `GRILL.md`. A fixture test the check needs is written into `checks/` first.
2. **Freeze.** `codebot start` turns the plan into `tasks.json` and **locks every check** (sha256 + a frozen copy). From now on only
   the harness changes task state; a worker that edits a check, a fixture, or `tasks.json` is detected, undone, and its attempt fails.
3. **Attempt.** For the next ready task the engine runs the check first (already green? done; red? its output is the error the worker
   sees), then runs the **native worker**: the model replies with one JSON action per turn under a JSON schema
   (`read_file`, `edit_file`, `write_file`, `run`, `replan`, `note_lesson`, `finish`). Every write is path-checked, syntax-checked and
   applied atomically; `edit_file` must match exactly once; malformed replies are bounced back. The harness builds the whole context
   (task, frozen check, last error, answers, decisions, repo map, relevant files, lessons) inside a token budget.
4. **Judge.** The frozen check decides, not the model. Pass: commit + tag in git (checkpoint). Fail: the **escalation ladder**.
5. **Escalate.** retry with the error, then reset to the last good commit and retry fresh, then split the task into smaller ones with
   their own checks, then an alternate prompt/temperature, then (optional, `ESCALATE_MODEL`) a larger local model, then **blocked**:
   it pushes the exact question and moves on to other tasks. `codebot answer` puts it back on the ladder.
6. **Budgets.** Caps per task, per project (minutes), total attempts; stuck signals (same failure N times, no progress in N attempts,
   a file edited back and forth) skip a rung. A run always terminates.
7. **Report.** At the end (and the first time it idles waiting on you) a morning report is saved and pushed.

Projects without `tasks.json` (old plans with no `check:` lines) keep running on the original loop unchanged.

## Safety and proof of offline operation

- Every command the model runs and every acceptance check runs in a throwaway Docker container: `--network none`, read-only root,
  all capabilities dropped, no-new-privileges, memory and pid limits, only the code dir writable, your home and the harness state
  invisible. It uses an image that is **already local** (`ubuntu:24.04`) with the host's `/usr` mounted read-only; nothing is pulled
  or created in Docker. Policy on top: no sudo, no network tools, no history-rewriting git, no package installs, no touching the state dir.
- `audit.jsonl` is a hash-chained log of every command (and refused command), file read/write with before/after hash, model request
  and check run. `codebot audit <project> [--probe]` verifies the chain, lists what ran and changed, shows that worker traffic is
  loopback-only, and with `--probe` tries to reach the internet from inside the sandbox and shows it fail. Exit code is non-zero on
  any warning (unsandboxed command, broken chain, ...).
- Honest limits: the `docker` group is root-equivalent (the harness holds it, the model does not); the engine and worker process are
  trusted code outside the container; the audit log is tamper-evident, not root-protected; `SANDBOX=auto` falls back to unsandboxed
  if Docker or the image is missing, and the audit then says so.

## Modes

A mode is a folder under `modes/` (`mode.json`, prompts, worker rules, a notes file, verifier helper scripts). Adding one needs no
code change. `codebot modes` lists them; `codebot plan <name> <dir> --mode <mode>` picks one.

- `code` (default): general coding with test-style checks.
- `re`: reverse engineering with [REA](https://github.com/morluto/rea): static analysis only (`rea` CLI + Ghidra run inside the
  offline sandbox with Node, the JDK and Ghidra mounted read-only; the target via `--target <path>`), an allowlist of safe commands,
  `rea capture-*/observe-*/setup/update/uninstall`, adb, gdb, lldb and strace denied, evidence-only findings with `[src: ...]` tags
  and a `findings_has.sh` verifier helper. `re` extends `code`.

## Memory

`memory/lessons.raw.jsonl` is append-only and never rewritten. Every attempt records which lessons were in its context and whether it
passed; lessons whose attempts pass clearly less often than the baseline get pruned (flagged, never deleted). `memory/lessons.md` is
generated from the raw log, best evidence first, under `LESSONS_MAX`. `memory/sizing.json` and `projects/<p>/stats.json` track which
task sizes succeed first try on this machine and feed the planner ("cut tasks to size S"). Edit `memory/expertise.md` freely.

## Per-project files

`project.env` (workdir, mode, overrides) · `PLAN.md` (rendered view; do not edit once frozen) · `tasks.json` · `checks/` + `checks.lock` ·
`GRILL.md` · `ANSWERS.md` · `attempts.jsonl` · `plan.log` (every plan change with reason) · `checkpoints.json` · `audit.jsonl` ·
`trace.jsonl` · `stats.json` · `report-*.md` · `loop.log` · `work/` (git worktree when the target is an existing repo; your checkout is untouched).

## Configuration (`config/codebot.env`)

Model/server: `CODEBOT_MODEL`, `OLLAMA_PORT`, `THINK`. Planning: `PLAN_STYLE=grill|quick`. Worker: `WORKER=native`, `CONTEXT_BUDGET`,
`WORKER_MAX_TURNS`, `WORKER_CONTEXT_TOKENS`, `WORKER_TEMPERATURE`, `RUN_TIMEOUT`. Ladder/budgets: `LADDER_TRIES`, `ALT_TEMPERATURE`,
`ESCALATE_MODEL`, `TASK_MAX_ATTEMPTS`, `STUCK_SAME_FAILURE`, `STUCK_NO_PROGRESS`, `PINGPONG_WINDOW`, `MAX_ITERATIONS`, `PROJECT_MAX_MINUTES`.
Safety: `SANDBOX=auto|docker|none`, `SANDBOX_IMAGE`, `SANDBOX_MEMORY`, `SANDBOX_RO`, `NTFY_ENABLED`, `PHONE_CONTROL`. Any key can be
overridden per project in `projects/<p>/project.env`.

## Running it on this machine

CPU only: about 4-6 tokens/second, hotter than the laptop likes. `codebot pause <p>` kills the running attempt and unloads the model
(verify with `ollama ps`); the watchdog timer (`codebot install-service`) restarts loops that die, keeping pauses and pending
questions. `THINK=off` is the default for unattended runs (hidden reasoning cost 10x on this CPU); the planning chat always thinks.
Close heavy apps for long runs and keep the vents clear. The NVIDIA driver (`sudo apt install nvidia-driver-580-open`, reboot) would
let Ollama offload layers to the 4 GB GPU.

## Tests

`tests/run.sh` runs ~190 tests in about 90 seconds with almost no CPU: a fake Ollama and scripted workers exercise the whole harness
(locking and tamper, checkpoints, replanning, budgets, ladder, worker validation, packing, memory, sandbox against real Docker,
audit chain, modes including a throwaway mode created with zero code edits, reports, and the real bash supervisor against a fake
ntfy server). Real-model runs are separate smoke tests (see `docs/ROADMAP.md`).

Design notes, decisions, and what was learned at each step: `docs/ROADMAP.md`.
