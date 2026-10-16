# codebot

The autonomous coding agent built for **small, slow, fully offline models** (ornith:9b on CPU through a private Ollama) running unattended for hours. Most harnesses
assume a frontier model and fall apart on a 9B one. codebot takes the decisions away from the model: the harness plans the work, packs the context, validates every
edit, judges every task with a check the model cannot change, checkpoints in git, escalates when stuck, and tells you in the morning what happened.

Nothing leaves this machine except the ntfy notifications you choose to keep (`NTFY_ENABLED=0` removes those too).

**Starting a new session or picking this up cold? Read `docs/HANDOFF.md` first** (current state, what is in flight, exact next steps, gotchas). `docs/ROADMAP.md` is the design
record. `modes/README.md` documents the mode format. `memory/README.md` documents the lesson store.

## Status (honest)

Built and tested: all 12 planned features (locked per-task checks, git checkpoints, budgets and stuck detection, escalation ladder, adaptive sizing, controlled replanning,
constrained outputs, deterministic context packing, evidence-backed memory, offline sandbox + audit, pluggable modes, morning report) plus a baseline guard for checks, a thermal
guard, a live preflight, a benchmark runner and a thermal sweep tool. Hundreds of tests (`tests/run.sh`, about 2 minutes, almost no CPU) cover the harness against a fake model,
real Docker and the real bash supervisor.

Proven with the real model so far: preflight passes (schema enforced by the server, 95% KV-cache reuse within an attempt, `num_ctx`/`keep_alive` as configured), and the easy
5-task benchmark suite passed 5/5 on the first attempt in 8 minutes. **Not yet proven with the real model:** the unattended planner after the harness-driven redesign, the hard suite
(ladder steps and a blocked task), the interactive grill-me planner end to end, the thermal sweep. Running the real model found three harness bugs the tests had missed, so treat the
test suite as necessary, not sufficient. Numbers and caveats: `docs/ROADMAP.md` ("Real-model results").

## Quick start

```
codebot                                  # the guided menu: pick or create a project, plan, start, watch live
codebot plan myproj ~/code/repo          # interactive "grill me" planning chat -> PLAN.md with a check per task
codebot check-plan myproj                # test every check against today's code (rejects ones that already pass or are broken)
codebot start myproj                     # freezes the checks and starts the unattended loop in the background
codebot status | log myproj | pause myproj | resume myproj | stop myproj
codebot answer myproj "T3: use the v2 API"   # reply when a task is blocked (or answer from your phone)
codebot report myproj                    # the morning report
codebot audit myproj --probe             # proof of what ran, and that it was offline
codebot diff myproj                      # review what it changed and how to merge it
codebot modes | tidy | help              # list modes / prune bad lessons / every command
codebot list | setup | notify-test       # all projects / start the private Ollama / send a test push
codebot install-service | resume-all     # watchdog timer (restarts loops that die) / what the timer runs
codebot re-config                        # rebuild the REA agent config after editing config/rea-tools.txt
codebot bench --preflight                # prove the context setup against the live model server
codebot bench --suite pyutils --plan fixed   # run a benchmark suite through the real pipeline (--plan auto = unattended planner)
codebot bench --thermal --minutes 4      # sweep threads / power profile / GPU for sustained speed vs heat
codebot eval gate MODEL            # model evaluation: speed/compatibility gate, mutant sets, scored planner probes (docs/MODEL_EVAL.md)
```

Subscribe to your ntfy topic (`config/codebot.env`) on your phone: pushes for start, blocked (with the exact question), tamper, budget stops, COMPLETE and the morning report.
Buttons: Pause / Resume / Status. Reply topic: `<topic>-reply` (`pause`, `resume`, `stop`, `status`, or an answer).

## How a run works (engine mode)

1. **Plan.** `codebot plan` is a "grill me" interview (13-area checklist, one question at a time, recommended answers, it challenges vague answers) and writes `PLAN.md`: small tasks,
   each with a `check:` (one shell command), `files:` and `size:`. Decisions go to `GRILL.md`. Test scripts the checks need are written into `checks/` first.
2. **Freeze.** `codebot start` turns the plan into `tasks.json` and **locks every check** (sha256 + a frozen copy). First the **baseline guard** runs each check against the untouched code:
   a check that already passes tests nothing, a broken one cannot test anything, and prose after the closing backtick is malformed; all are rejected, logged to `plan.log`, and nothing is
   frozen. After freezing only the harness changes task state; a worker that edits a check, a fixture, or `tasks.json` is detected, undone, and its attempt fails.
3. **Attempt.** For the next ready task the engine runs the check first (already green? done; red? its output is the error the worker sees), then runs the **native worker**: the model
   replies with one JSON action per turn under a JSON schema (`read_file`, `list_dir`, `edit_file`, `write_file`, `run`, `replan`, `note_lesson`, `finish`). Every write is path-checked,
   syntax-checked and applied atomically; `edit_file` must match exactly once; malformed or cut-off replies are bounced back. The harness builds the whole context (task, frozen check,
   last error, answers, decisions, repo map, relevant files, lessons) inside a token budget, and guarantees request + reply fit in `NUM_CTX`.
4. **Judge.** The frozen check decides, not the model. Pass: commit + tag in git (checkpoint). Fail: the **escalation ladder**.
5. **Escalate.** retry with the error, then reset to the last good commit and retry fresh, then split the task into smaller ones with their own checks, then an alternate prompt/temperature,
   then (optional, `ESCALATE_MODEL`) a larger local model, then **blocked**: it pushes the exact question and moves on to other tasks. `codebot answer` puts it back on the ladder.
6. **Budgets.** Caps per task, per project (minutes), total attempts; stuck signals (same failure N times, no progress in N attempts, a file edited back and forth) skip a rung. A run always terminates.
7. **Report.** At the end (and the first time it idles waiting on you) a morning report is saved and pushed.

Projects without `tasks.json` (old plans with no `check:` lines) keep running on the original opencode loop unchanged.

## Safety and proof of offline operation

- Every command the model runs and every acceptance check runs in a throwaway Docker container: `--network none`, read-only root, all capabilities dropped, no-new-privileges, memory and pid
  limits, only the code dir writable, your home and the harness state invisible. It uses an image that is **already local** (`ubuntu:24.04`) with the host's `/usr` mounted read-only;
  nothing is pulled or created in Docker. Policy on top: no sudo, no network tools, no history-rewriting git, no package installs, no touching the state dir.
- The code dir is a git worktree in `workspaces/<project>/` when the target is an existing repo (your checkout and uncommitted changes are never touched). It must never be inside
  the state dir (`projects/<project>/`), which the worker is barred from; `codebot start` migrates older layouts.
- `audit.jsonl` is a hash-chained log of every command (and refused command), file read/write with before/after hash, model request and check run. `codebot audit <project> [--probe]`
  verifies the chain, lists what ran and changed, shows that worker traffic is loopback-only, and with `--probe` tries to reach the internet from inside the sandbox and shows it fail.
  Exit code is non-zero on any warning (unsandboxed command, broken chain, ...).
- Honest limits: the `docker` group is root-equivalent (the harness holds it, the model does not); the engine and worker process are trusted code outside the container; the audit log is
  tamper-evident, not root-protected; `SANDBOX=auto` falls back to unsandboxed if Docker or the image is missing (the audit then says so). `bwrap` does not work on this machine (AppArmor).

## Modes

A mode is a folder under `modes/` (`mode.json`, prompts, worker rules, a notes file, verifier helper scripts); adding one needs no code change (format: `modes/README.md`).
`codebot modes` lists them; `codebot plan <name> <dir> --mode <mode>` picks one.
- `code` (default): general coding with test-style checks.
- `re`: reverse engineering with [REA](https://github.com/morluto/rea): static analysis only (`rea` CLI + Ghidra run inside the offline sandbox with Node, the JDK and Ghidra mounted
  read-only; the target via `--target <path>`), an allowlist of safe commands, `rea capture-*/observe-*/setup/update/uninstall`, adb, gdb, lldb and strace denied, evidence-only findings
  with `[src: ...]` tags and a `findings_has.sh` verifier helper. `re` extends `code`.

## Memory

`memory/lessons.raw.jsonl` is append-only and never rewritten. Every attempt records which lessons were in its context and whether it passed; lessons whose attempts pass clearly less often
than the baseline get pruned (flagged, never deleted). `memory/lessons.md` is generated from the raw log, best evidence first, under `LESSONS_MAX`. `memory/sizing.json` and
`projects/<p>/stats.json` track which task sizes succeed first try on this machine and feed the planner ("cut tasks to size S"). Edit `memory/expertise.md` freely. A project can point `MEMORY_DIR` (in its `project.env`) at its own folder; benchmark trials do this so they never read or change the shared memory.

## Per-project files (`projects/<name>/`)

`project.env` (workdir, `RUNDIR`, mode, overrides) · `PLAN.md` (rendered view; do not edit once frozen) · `tasks.json` + `tasks.json.sha` · `checks/` + `checks.lock` · `GRILL.md` · `SPEC.md`
(bench only) · `ANSWERS.md` · `attempts.jsonl` · `plan.log` (every plan change and guard rejection, with reason) · `checkpoints.json` · `audit.jsonl` + `audit.head` · `trace.jsonl` (every
model turn with timings) · `thermal.jsonl` · `stats.json` · `report-*.md` · `loop.log` · `plan-auto.log` and `plan-round-*.prompt.md` (bench planner). The code dir is separate:
`workspaces/<name>/` (worktree) or your own folder.

## Configuration (`config/codebot.env`, every key documented inline there)

Model/server: `CODEBOT_MODEL`, `OLLAMA_PORT`, `NUM_CTX` (must equal the Modelfile and opencode configs; a test enforces it), `KEEP_ALIVE`, `THINK`. Planning: `PLAN_STYLE=grill|quick`,
`CHECK_BASELINE_GUARD`, `REGRESSION_GATE` (a task is done only when every done task still passes), `PLAN_PER_ITEM` (one planner call per numbered spec item). Worker: `WORKER=native`, `CONTEXT_BUDGET`, `WORKER_MAX_TURNS`, `WORKER_CONTEXT_TOKENS`, `WORKER_TEMPERATURE`, `RUN_TIMEOUT`. Ladder/budgets: `LADDER_TRIES`,
`ALT_TEMPERATURE`, `ESCALATE_MODEL`, `TASK_MAX_ATTEMPTS`, `STUCK_SAME_FAILURE`, `STUCK_NO_PROGRESS`, `PINGPONG_WINDOW`, `MAX_ITERATIONS`, `PROJECT_MAX_MINUTES`. Notifications: `NTFY_SERVER`, `NTFY_TOPIC`, `NTFY_TOKEN` (protected topics), `NOTIFY_EACH_TASK`. Legacy opencode loop only: `ITER_TIMEOUT`, `STALL_LIMIT`, `LESSONS_MAX`. Less common:
`CHECK_TIMEOUT` (seconds per acceptance check), `WORKER_MAX_TOKENS` (generation limit per worker turn), `WORKER_CMD` (replace the native worker, used by tests), `IDLE_POLL` (seconds between
checks while waiting for your answer), `SANDBOX_PIDS`, `THERMAL_MAX_WAIT`. Safety: `SANDBOX=auto|docker|none`,
`SANDBOX_IMAGE`, `SANDBOX_MEMORY`, `SANDBOX_RO`, `NTFY_ENABLED`, `PHONE_CONTROL`. Heat: `THERMAL_GUARD`, `THERMAL_MAX`, `THERMAL_RESUME`. Any key can be overridden per project in `project.env`.

## Running it on this machine

CPU only (the RTX 3050 Ti has no NVIDIA kernel driver installed, so Ollama cannot offload): about 6 tokens/second generation and about 30 tokens/second cold prompt processing.
The laptop sits at 90-93 C under load; the thermal guard pauses model calls at 92 C and resumes at 84 C. llama.cpp already uses 4 threads (the physical cores). Measure the options with
`codebot bench --thermal`. The model (qwen35 architecture) is a hybrid/recurrent one, so the KV cache is reused within an attempt (append-only conversation) but NOT across attempts or after a
context compaction; a prompt that diverges early is fully re-evaluated. `codebot pause <p>` kills the running step and unloads the model (check `ollama ps`); the watchdog timer
(`codebot install-service`) restarts loops that die, keeping pauses and pending questions. `THINK=off` is the default for unattended runs; the planning chat always thinks. Close heavy apps
for long runs; a cooling pad (or repaste) helps more than any setting. `sudo apt install nvidia-driver-580-open` + reboot would let Ollama offload layers to the GPU.

## Capability Ladder Benchmark

`codebot bench list` / `codebot bench run --level N` / `codebot bench run --suite ladder --repeats 3` / `codebot bench run --suite growing` (also in the menu, option `b`). Twelve hidden-test-scored projects of rising difficulty plus a growing project; each planned task gets its own strict time budget (90 min), no limit on the run. Full description, metrics and known limits: `docs/BENCHMARK.md`.

## Tests

`tests/run.sh [file]` runs the whole suite in about 2 minutes with almost no CPU: a scripted fake Ollama and scripted workers exercise the harness (locking and tamper, checkpoints,
replanning, budgets, ladder, worker validation, packing and context limits, memory, sandbox against real Docker, audit chain, modes including a throwaway mode created with zero code edits,
reports, the baseline guard, bench tooling, and the real bash supervisor against a fake ntfy server). `tests/test_r_docs.py` fails if these docs drift from the code (commands, config keys,
paths). `tests/test_p_regressions.py` holds the bugs that only real-model runs found.
