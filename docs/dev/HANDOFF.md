# codebot: handoff (read this first in a new session)

**Current state (2026-10-21).** Drafts mode (`codebot new`, then `codebot start`) is the way codebot works; the task pipeline (`codebot plan`)
is kept working but no longer developed. This machine runs `coder30b-iq3` (Qwen3-Coder-30B-A3B, 3-bit) through the private Ollama
(`config/local.env`; `docs/LARGE_MODELS.md`), the shipped default is still ornith:9b. A spec's worked examples are the only thing drafts mode
can check, and since 66ae7b8 they can also show error output, exit statuses and standard input. **Read `docs/dev/REVIEW-2026-10-21.md`
before tuning anything:** every drafts-mode rule was chosen and measured on the same three tasks (level_01, level_03, calc_go), so the next
changes must be judged on held-out tasks with several seeds. The sections below are the history in order (oldest first in each section).

Last updated before that: 2026-10-19, after the baselines showed a plain whole-program loop beating the task pipeline and the user chose to rebuild around it: **drafts mode** (`codebot new`, `lib/codebot/drafts.py`, section "Drafts mode" below) is now the recommended way; the task pipeline stays as it was. Everything described here is committed (`git log` in this repo).
This file is the single place that says what the project is, what state it is in, what is in flight, and what to do next. `docs/dev/ROADMAP.md` has
the long design record (decisions, per-step learnings, real-model results). `README.md` is the user-facing description.

## 1. What codebot is

A local-only autonomous coding agent for **small, slow, offline models**: ornith:9b on CPU through a private Ollama, running unattended for hours.
Identity (the user's words): "the autonomous coding agent built for small, slow, fully offline models ... Every feature exists to make a weak
local model finish real projects reliably without supervision." The harness, not the model, plans the work, packs the context, validates every
edit, judges every task with a check the model cannot change, checkpoints in git, escalates when stuck, and reports in the morning.

Non-negotiable constraints the user set: local only (no cloud compute, no new network dependencies beyond the private Ollama on :11500); do not
break existing commands or the `codebot` menu UI; extend before rewriting (note why if rewriting); **stop and ask before anything destructive or hard
to reverse, or anything outside the codebot directory**.

## 2. Where everything lives

Repo: the codebot checkout (here `~/Documents/Development/codebot`) (git, branch with ~27 commits; `projects/ models/ tools/ logs/ workspaces/ bench/runs/ bench/results/` are git-ignored).

| path | what |
|---|---|
| `bin/codebot` | bash CLI + supervisor loops (legacy loop, `loop_engine`, `loop_drafts`), `new` / `check-spec` (drafts projects), notify/ntfy, phone control, watchdog install. Dispatch is the `case` at the bottom. |
| `bin/codebot-engine` | python CLI over the engine: freeze, step, status, render (re-render PLAN.md from tasks.json), answer, audit, report, check-plan, check-spec (a spec file: which items have examples), modes, mode-info, prompt-template, sizing, split, memory, plan-op, reset. For a drafts project step/status/report go to `drafts` |
| `bin/codebot-ui` | the interactive menu (`codebot` with no args) |
| `bin/codebot-bench` | `codebot bench ...` (preflight, suites, thermal sweep; the subcommands `list/run/status/report/resume/cancel/validate/coverage` go to `lib/codebot/capbench/`, see `docs/BENCHMARK.md`) |
| `bin/codebot-mkconfig` | builds `config/opencode-re.json` (REA MCP config; data named in `modes/re/mode.json`) |
| `lib/codebot/*.py` | the engine (python stdlib only): `drafts` (drafts mode: one whole-program round per step, restarts, best version delivered and committed, coverage of spec items by examples; the prompts are bench/baselines/bare_py.py's text), `engine` (step state machine), `tasks` (tasks.json store + integrity), `checks` (freeze/lock/tamper/run), `guard` (baseline guard), `planparse`, `planops` (add/split/reorder only), `gitops` (baseline commit, checkpoints, reset), `budgets` (stuck signals), `ladder`, `sizing` (split + stats), `splitter` (model-driven task splitter, the default SPLIT_CMD with the native worker; emits declarative assertions), `assertions` (declarative checks: the trusted assertion runner, copied into every project's `checks/` as `assert_runner.py`; exit 0 holds / 1 real failure / 2 unusable spec), `checkspec` (assertion JSON schema, spec files, check command, PLAN.md rendering), `planner` (native unattended planner: one constrained call + harness-driven repair rounds, used by `codebot bench --plan auto`), `worker` (native worker), `packer` (context), `specdoc` (the spec as a task sees it: the introduction plus the requirement the task implements, from `spec_items.json`, which the planner writes), `grounding` (checks grounded in the spec: its worked examples become harness-written assertions; `screen` drops model assertions that cannot hold for a program following the spec), `rederive` (before a split: assertions that failed in every attempt are judged against the spec and may be replaced, `checks.replace_check`), `ollama` (client), `tokens` (calibrated estimate), `thermal`, `taskclock` (per-task wall-clock budgets: one persisted budget per root task, enforced by killing the worker's process group; see ROADMAP), `proc` (run a shell command and kill its whole process tree on timeout), `sandbox` (Docker + policy), `audit` (hash chain), `memory` (evidence-backed lessons), `modes`, `report`, `preflight`, `bench`, `thermalbench`, `config` |
| `lib/codebot/gui/` + `shape`, `projstate`, `jobs` | the window, `codebot gui` (`docs/dev/GUI_PLAN.md`): `gui/server.py` (127.0.0.1 only; POST needs header `X-Codebot: 1`, its own Origin and Host; CSP), `gui/api.py` (JSON API; every action runs the `codebot` CLI with an argument array; demo mode refuses every POST but check-spec), `gui/settings.py` (layered settings; writes only known keys, never a value bash would expand), `gui/runner.py` (the Run button: the built program on a pty, **not sandboxed**), `gui/actions.py` (detached long actions), `gui/sysinfo.py`, `gui/static/` (vanilla ES modules, text nodes only). `shape` (the design conversation: describe, questions, plan + spec, Build; refuses a plan without examples; the closing review that `loop_drafts` runs via `python3 -m codebot.shape review`), `projstate` (one project's state for every interface), `jobs` (one model job at a time: flock on `logs/model-job.lock`; **not yet taken by `bin/codebot`**, so `current()` also counts loops and benchmark runners found by pid file). Tests: `tests/test_gui.py`, `tests/test_shape.py`, `tests/test_runner.py` |
| `lib/codebot/capbench/` + `bench/ladder/catalog/` | Capability Ladder Benchmark (12 levels + growing notes project, hidden reference tests, per-task 90 min budget via `taskclock` setting `TASK_MAX_MINUTES`, resumable runs in `bench/results/ladder/`). Modules: `manifests`, `reference`/`refrun` (hidden-test oracle), `workspace`, `runner`, `results`, `metrics`, `classify`, `reports`, `notify`, `cli`. Tests `tests/test_v_capbench.py`, `tests/test_u_taskclock.py`. Docs: `docs/BENCHMARK.md` |
| `lib/codebot/pipeline/` + `pipelines/templates/` | Modular multi-agent orchestration (`codebot.pipeline/v1`): validate/load, sequential scheduler, mailbox Q&A, artifacts, `sees`, Team Designer, drafts bridge, SVG canvas GUI. Templates include solo, reviewers, debate, small-pods. Docs: `docs/architecture/modular-agent-orchestration.md`. Tests: `tests/test_pipeline_config.py`, `tests/test_pipeline_runtime.py`, GUI cases in `tests/test_gui.py`. |
| `modes/<name>/` | `code` (default) and `re` (reverse engineering with REA, `extends` code). `mode.json` + prompts (`plan.md`, `plan-quick.md`, `plan-auto.md`, `plan-repair.md`, `work.md`) + `worker.md` + notes + `verifiers/` |
| `config/codebot.env` | all settings (documented inline). `config/Modelfile` (num_ctx 32768), `config/opencode.json`, `config/opencode-re.json` (generated) |
| `memory/` | `expertise.md` (user-edited), `lessons.raw.jsonl` (append-only), `lessons.status.json`, generated `lessons.md`, `sizing.json`. Shared by all projects unless a project sets `MEMORY_DIR` (benchmark trials do: `projects/<trial>/memory`). Committed to git but rewritten at run time, so runs leave it modified |
| `tests/` | ~597 test methods (`tests/run.sh [file]`, about 7 min for all, almost no CPU). `test_z_drafts.py` covers drafts mode; `test_pipeline_*.py` cover modular teams. `fake_ollama.py` is the scripted fake model server. Files `test_a_...` to `test_q_...` follow the roadmap steps; `test_p_regressions.py` holds bugs found by real-model runs; `test_u_taskclock.py` and `test_v_capbench.py` cover the benchmark |
| `bench/suite/pyutils` (easy, 5 tasks) and `bench/suite/pyhard` (hard, 5 tasks, one needs a human) | each has `spec.md`, `seed/`, `checks/`, `plan.md`, `reference/` (proven-correct solution used by tests) |
| `projects/<name>/` | per-project state (see README "Per-project files"). `workspaces/<name>/` = git worktrees (code dirs must NOT be inside the state dir) |

Models: `codebot-model` (ornith:9b, default) and `qwen-coder-model` (qwen2.5-coder:7b, for comparison only; `config/Modelfile.qwen-coder`). `codebot bench --model <name>` runs a whole bench with one model; `codebot bench --compare <run> <run>` prints a side-by-side table; `--plan-only` samples a planner in about 3 minutes. Results so far: ornith is the better worker (5/5 in 1 attempt each vs 4/5 with 2 attempts for qwen on the same plan; qwen is 18% faster per token but needs 4.6x the turns) and, with the revised prompt, both models write mostly-but-not-fully sound plans. See `docs/dev/ROADMAP.md`, section "Model comparison".

Environment facts: Acer Predator PT314-51s (Triton 300 SE), i7-11375H (4 cores / 8 threads), 15 GB RAM + 16 GB swap (heavily used), RTX 3050 Ti 4 GB with the **NVIDIA 595-open driver loaded** (about 35% of the model's layers are offloaded automatically; forcing `num_gpu=99` runs out of VRAM), Ubuntu 24.04 / KDE. Private Ollama 0.40.3 binary in `tools/ollama`, models in `models/`, port
**11500** (the system Ollama 0.22 on :11434 is too old for ornith; leave it alone). Model `codebot-model` = `ornith:9b` (Q4_K_M, 5.6 GB, architecture `qwen35`, a
hybrid/recurrent model) + 32k context. opencode 1.19.0 lives in `tools/node_modules`. Docker works for this user (group `docker`); sandbox image `ubuntu:24.04` is
already local. REA: `rea` 6.4.0 (`~/.local/node/bin/rea`), Ghidra 12.1.4 (`~/.local/ghidra_12.1.4_PUBLIC`), JDK 21 (`~/.local/jdk-21`). ntfy topic in `config/local.env` (rotated 2026-10-16; the old one was in git history)
(reply topic `...-reply`). A systemd **user** timer `codebot.timer` restarts loops that die (only projects with a `.active` file). `codebot` is symlinked into `~/.local/bin`.

## 3. How a run works

**Drafts mode** (`WORKFLOW="drafts"` in project.env; `codebot new <name> <spec.md> [dir]` creates it, `codebot check-spec` shows which spec items
have examples). `codebot start` does the usual git prep, then the bash supervisor (`loop_drafts`) calls `codebot-engine step` once per round:
`drafts.step` asks the model for every file whole (first draft: spec + the baseline code dir; repair: spec + the draft's best version + what
failed), extracts the files, compiles them, runs the spec's build command and examples (`grounding`, run by the trusted assert runner) in the
sandbox, and keeps the round's record in `projects/<name>/drafts/`. A draft that has not improved for `DRAFTS_PATIENCE` rounds is dropped; each new
best version is written to the code dir and committed. It stops when every example passes, at `DRAFTS_MINUTES` of round time or at
`DRAFTS_MAX_ROUNDS`. Pause/resume/stop, phone control, the watchdog, `status`, `report` and `diff` work as for task projects.

**Task (engine) mode**, in one paragraph:

`codebot plan` (interactive "grill me" planner via opencode TUI) writes `PLAN.md` where every task has `check:` / `files:` / `size:`. `codebot start` freezes it:
the **baseline guard** rejects checks that already pass, are broken, or have prose after the backticks; checks and fixtures are sha256-locked; `tasks.json` is
harness-owned. The loop calls `codebot-engine step` repeatedly: pre-run the check, run the **native worker** (JSON-schema actions, edits validated, context packed by
the harness, commands in an offline Docker sandbox), run the frozen check, checkpoint in git on pass, otherwise climb the **ladder** (retry, reset+retry, split, alternate
prompt, optional larger model, blocked + a pushed question). Budgets, stuck signals, an audit log (`codebot audit --probe`), evidence-backed memory, thermal guard,
morning report (`codebot report`). Projects without `tasks.json` still run on the original opencode loop.

## 4. Status of the roadmap (`docs/dev/ROADMAP.md`)

All 12 requested features (1 locked per-task checks, 2 git checkpoints, 3 budgets/stuck, 4 ladder, 5 adaptive sizing, 6 controlled replanning, 7 constrained outputs,
8 context packing, 9 evidence memory, 10 sandbox + audit, 11 pluggable modes, 12 morning report) are **implemented and tested** (fake model +
real Docker + the real bash supervisor; see the verification status in section 2). Added after: baseline guard for checks, explicit `num_ctx`/`keep_alive`, stable-prefix context ordering, hard context fit,
thermal guard, preflight, bench tooling, thermal sweep tool, harness-driven planner repair rounds.

Real-model (ornith:9b) evidence so far (details in ROADMAP "Real-model results"):
- **Preflight passes** on the live server: num_ctx 32768 identical everywhere, keep_alive 30 min, JSON schema genuinely enforced by the server, KV cache reuse 95%
  across turns of one attempt, token ratio calibrated (3.08 chars/token). Limitation: a prompt that **diverges early** is fully re-evaluated (hybrid model; cache
  checkpoints only at the end of a prompt), so no reuse across attempts or after a context compaction.
- **bench-fixed-2 (easy suite, fixed plan): 5/5 tasks verified, 1 attempt each, 8 min wall**, 3-5 turns per task, 0 malformed replies, generation ~6 tok/s, cold prompt
  ~29.5 tok/s, 0 ladder steps (suite too easy to exercise them), package temp max 93 C (thermal guard paused once for 20 s).
- First real run found harness bugs no test had caught (worktree inside the state dir; `$CODEBOT_CHECKS` unset in worker commands), and a second class: the opencode
  agent config had `"patch": false`, which silently removed write AND edit tools (fixed + tested against opencode itself).
- Unattended planner attempt 1 (opencode, 9B): wrote GRILL.md/PLAN.md/4 fixtures, but never ran `codebot check-plan`, read "rewrite until OK" as "make the checks pass",
  and started writing the implementation into a stray `projects/<p>/code/`. Offline guard run on its plan: 2 of 4 checks rejected (prose after the backtick). Fix: the
  harness now drives the planner in rounds (`bench.plan_auto`): runs check-plan itself, undoes code changes (recorded as violations), feeds back the exact report. **This
  new loop has not yet been run with the real model.**

## 5. In flight / not done (the honest list)

**Pipeline graph / modular teams (2026-11-11).** Implemented: `codebot.pipeline/v1` JSON, sequential scheduler (one inference + `jobs.Hold`), WAITING/Q&A mailbox, artifacts, enforced `sees`, Team Designer, `pipeline init` (upgrade drafts→pipeline), drafts bridge, debate/small-pods/nested templates, nested-team expansion (`parent-child` ids), pod RESULT to managers, link kinds agent/repository/both, cancel, CLI (`validate|init|design|step|run|status|cancel`), `loop_pipeline`, GUI `#/pipeline/<name>`. Docs: `docs/architecture/modular-agent-orchestration.md`. Still later: Scratch/Blockly, real-model A/B vs drafts. Drafts remains the default.

**The window (2026-10-22).** Built and reviewed; open items in order: (1) `bin/codebot` does not hold `logs/model-job.lock` yet (fd 9 in `__loop`, `jobs check` in `cmd_start`, `9>&-` for `ollama serve`, the benchmark runner's Hold); once it does, the closing review must run under the loop's lock instead of taking its own `jobs.Hold`. (2) Run executes the model's program unsandboxed on the host (a terminal program could run in the Docker sandbox; a windowed one needs the display). (3) The closing review is the model's opinion and has been confidently wrong (a Chip-8 XOR "serious" finding); label it or turn findings into examples. (4) No way to change a finished build ("a new run needs a new project"). (5) The design flow has never been measured on held-out ideas.

**Capability Ladder Benchmark (`docs/BENCHMARK.md`): built, validated, smoke-tested once.** Catalog of 12 levels + 8-stage growing project, all oracles validated (`codebot bench validate`). Smoke run (level 1, 4 min per task): hidden tests 41/64. **First full-budget run** (level 1, 90 min, 3 trials, `20261015-180429-l1-full`): hidden tests 78% / 47% / 73% (median 73%, below the 80% bar), 0 timeouts, 7 VERIFICATION_GAP; the planner's assertions (impossible `"expect"` args, weak coverage) decided the score. Details in `docs/BENCHMARK.md`. Done 2026-10-16 (`assertions.validate`, so the guard sees exit 2 = broken and the planner repair round / splitter drop them): a schema field name (`expect`, `raises`, `stdout_contains` ...) inside `args`/`kwargs`/`argv`, and `python3 -c` code that does not compile. These would have caught all three impossible checks of trial 2. Not done: an arity-consistency rule (a function legitimately takes optional arguments, so differing argument counts are not evidence; the leaked-field rule covers the case seen). Also 2026-10-16: 23 of the 65 failed hidden tests of that run were regressions (a later task broke a verified one; nothing re-ran earlier checks), so the engine now has a regression gate (`REGRESSION_GATE=1`: done only when every done task still passes), A planner-prompt change asking for one assertion per stated rule (4-12 per task) was REVERTED the same day: with it ornith packed items 1 and 2 into one 12-assertion task and ended the plan (5 of 6 first rounds on 2026-10-16 had 1 task; trials 1-2 of run 20261016-065138-l1-gate failed planning) and it copied the expected value into args (`slugify(123, "TypeError")`). Coverage needs another route that does not touch the task structure, e.g. a separate per-task assertion pass. Planning rounds now log their reason and keep the reply (`plan.reply.<n>.json`). Splits: a split child whose check already passes (that part is done) or is broken is now dropped and the rest of the split kept; before, one such child rejected the whole split (run 20261016-073141-l1-gate3, T2 `truncate`: split rejected, then blocked; run cancelled at the user's request to fix it). Known planner flake: ornith sometimes returns a 1-task plan for an 8-item spec (3 of 6 first rounds with the 2026-10-15 prompt); the repair rounds have recovered so far. Candidate fix: set the plan schema's minItems to the spec's item count. **Cancel race (found and fixed 2026-10-16):** a cancel that lands right after a trial's planning lets its loop survive: `drive_loop` starts the loop, sees the cancel, runs `codebot stop` before the loop has written `loop.pid`, and `stop` says "not running". The orphan loop of run 20261016-072308-l1-gate2 ran 07:33-10:02, shared the model with run 20261016-073141-l1-gate3 (its timings are not clean) and held `logs/model.lock` so run 20261016-095343-l1-gate4 waited 8 min (not charged to any task budget). Fix: check cancel before starting the loop and confirm the loop is dead after stop. Since the review on 2026-10-15 each trial has a private memory (`MEMORY_DIR`); the smoke run and every earlier bench run wrote into the shared `memory/` (the user chose to keep that data: it is 48 real tasks, committed in `memory/sizing.json` and `memory/lessons.status.json`).

Project review (2026-10-15), weakest points in order: (a) the planner's assertions are often unsound or too weak (0/3 fully sound plans on pyutils after the grounding fixes; 3 verification gaps in the smoke run), and no audit exists that works without a reference solution; (b) no capability numbers; (c) no user-facing native planner command (item 4 below); (d) interactive planner still on raw shell checks; (e) splitter with assertions only fake-tested; (f) heat limits how much real evidence can be gathered.

Older items:

Done since the last handoff (details and numbers in `docs/dev/ROADMAP.md`): NVIDIA driver installed and offload measured (thermal sweep, auto-fit puts about 35% of the layers on the 4 GB GPU: 3.4x the throughput of CPU-only, still 90+ C); hard suite run twice with the real model (4/5 verified, T5 blocked as designed, all ladder steps seen); the splitter bug found there fixed (guard on split children, children inherit `files`); the free-form opencode planner found unusable after three real runs and replaced by **declarative checks** (planner and splitter emit assertion data, the harness owns the runner, paths and check commands, exit status 0/1/2 is a contract the guard enforces). First real run of the new planner (`bench-auto-6`): plan in 178 s with 0 rejected checks, 5/5 tasks verified, 1 attempt each, 6.7 min.

1. **Harder real-model evidence for the new planner.** `bench-auto-6` used the easy suite: no ladder step fired. Needed: a hard-suite spec run through `--plan auto` (needs a `spec.md` for `bench/suite/pyhard`, which has only a fixed plan today), and a few repeated runs to see how often the planner needs repair rounds and writes weak or unsatisfiable assertions.
2. **Splitter with declarative assertions has only been tested with the fake model** (`tests/test_s_declarative.py`, `tests/test_h_worker.py`). It needs a real run where a task actually splits (the hard suite does this).
3. **Interactive grill-me planner** (`codebot plan demo ~/scratch`) still uses the free-form format with raw shell checks (protected by the guard's static checks). Decide whether it should also produce assertions; not live-tested end to end with the `check:` format.
4. **No user-facing command for the native planner** yet: it is reachable through `codebot bench --plan auto` only. A `codebot plan-auto <name>` that reads a spec file is the obvious next step.
5. **Assertion types may need growing** (for example: run a command and compare to a file, import-time side effects, exit status only). Measure how often the planner needs a raw shell check before adding types.
6. Loose ends: `bench-fixed-2` kept as a reference result (can be deleted); the legacy RE project `re1` is stopped (delete or re-plan in engine mode); `SANDBOX=auto` could become `docker`; the laptop still runs at 90-96 C under load (power-saver gives 65 C at 54% speed; a cooling pad or repaste is the real fix).

## 6. Next steps, in order (copy-paste)

Current (2026-10-21; drafts mode):
```
codebot setup                                     # starts the private Ollama (with LLAMA_ARG_CACHE_RAM=0)
tests/run.sh                                      # full suite, about 7 min; it can reach the real model server, so never while a model job runs
codebot bench --preflight                         # must say PREFLIGHT OK for the configured model
codebot check-spec SPEC.md                        # which requirements have examples
codebot new NAME SPEC.md ~/code/NAME && codebot start NAME     # a project; review with codebot diff NAME
codebot bench run --level 2 --mode drafts --task-minutes 60 --seed 1 --repeats 3   # held-out tasks need examples first (REVIEW-2026-10-21)
```

Older (2026-10-15/16, the task pipeline):
```
cd ~/Documents/Development/codebot        # your checkout
codebot setup                      # starts the private Ollama (the model is unloaded after a reboot; the first request reloads it)
tests/run.sh                       # full suite, about 2.5 min
codebot bench --preflight          # about 5 min, must say PREFLIGHT OK
nohup codebot bench --suite pyutils --plan auto --name bench-auto-N --max-minutes 120 --plan-minutes 40 > /tmp/b-auto.out 2>&1 &   # about 10 min with the native planner
nohup codebot bench --suite pyhard  --plan fixed --name bench-hard-N --max-minutes 90 > /tmp/b-hard.out 2>&1 &                    # about 70 min; one model job at a time, never both
codebot bench run --level 1 --repeats 3      # the first real capability number: up to 90 min per task, several hours per trial; only when the user asks (overnight)
codebot bench run --level 1 --repeats 1 --mode reference-plan   # then: does the validated plan raise the hidden-test rate? (planning vs implementation)
```
Run model jobs **one at a time** and watch `sensors | grep Package`. Use a `Monitor` that reports only state changes (tasks.json statuses), not per-turn noise. After each run: put the results in `docs/dev/ROADMAP.md`, commit, and tell the user what was found including what failed. Do not edit `lib/` while a bench is running (every engine step imports it fresh).

### How to change the harness (agreed with the user on 2026-10-16, after the same problem kept coming back in different forms)

- **Salvage, do not reject wholesale.** Wherever model output enters (plan, repair round, split, assertions): repair when the intent is unambiguous, drop the bad
  item and keep the rest, ask the model again only as a last resort (a 9B model mostly repeats its mistake), fail only when nothing usable is left. Log every drop/repair.
  Audit of the entry points (2026-10-16), all implemented the same day: the planner drops single unusable assertions (not the task), repairs calls with the
  expected value copied into args and lists spread over args (`checkspec.repair_plan`, plan-wide arity), and after the last repair round keeps the good tasks
  (`plan_salvaged`) instead of failing the plan; the splitter applies the same repairs with the parent's assertions as context, may split into ONE usable child,
  and logs `split_proposal` + keeps `split.reply.<task>.json`; the worker already handles each action separately (kept).
- **Per-item planning** (`PLAN_PER_ITEM=1`, default when the spec has 2+ numbered items): one small model call per item, so a plan cannot collapse, merge items
  or pad itself (the 1-task plans, and with minItems=8 a plan of 4 two-function tasks + 4 filler tasks, both seen 2026-10-16). goal/context/done come from the
  spec text. Replies kept as `plan.reply.<round>.item<n>.<try>.json`. Not yet measured with the real model.
- **Replay corpus before real runs.** `tests/corpus/` holds every real assertion a planner/splitter froze (judged against the reference solution) and the raw plan
  replies; `tests/test_w_corpus.py` replays them in about 2 s. A rule must never reject or change a sound corpus assertion; the ratchet counts (impossible assertions
  let through, collapsed plans) may only go down. Rebuild with `python3 tests/corpus/build_corpus.py` after every real run. Corpus facts: 105 of 282 real assertions
  fail on the reference; the splitter is much worse than the planner on level 1 (12/21 unsound vs 11/80).
- **Structure over wording.** Constraints the harness enforces (schema minItems, required fields, deterministic repair) work; prompt wording changes have side
  effects no test catches (the coverage wording collapsed plans). Change the prompt only with a planning-only probe (3-5 plans, about 10 min each) against the reference.
- **Full runs measure; they are not for finding bugs.** Do not tune against level 1 alone: confirm on level 2 or 3 before calling a fix general.

### Public release prep (2026-10-16)

MIT `LICENSE`, `install.sh` (pinned Ollama 0.40.3 with sha256, `ornith:9b` -> `codebot-model`, optional opencode, writes `config/local.env`), `bin/codebot-doctor` (`codebot doctor`), `SECURITY.md`, `CONTRIBUTING.md`, README rewritten for new users. Settings are split: `config/codebot.env` = tracked defaults (notifications and phone control OFF, no topic), `config/local.env` = this machine (untracked; read after it by bash and by `config.home_env`). The old ntfy topic was in git history and was rotated. Memory run data, `*.lock` and the generated `config/opencode-re.json` are untracked. The user chose: MIT, a fresh one-commit public repo (this repo keeps its history), empty memory starters.

### Model evaluation (started 2026-10-16)

Method, scores, candidates and commands: `docs/MODEL_EVAL.md`. 10 candidates pulled into `models/` (the control `ornith:9b` is never updated in place). Per-role settings in `lib/codebot/roles.py`; scorer, mutants, gate and planner probe in `lib/codebot/evalkit/`; `codebot eval ...`. First finding: the control is Ornith 1.0 (Qwen3.5-9B + agentic-coding RL) and codebot runs every call with thinking off; its plans have better coverage than the hand-written reference plan but 1 in 3 tasks carries an impossible assertion.

### Design review fixes (2026-10-17)

The Go calculator builds (`calc_go`, 6 setups + a control rerun) showed the harness, not the model, was the bottleneck: only 3 of the control rerun's
41 frozen checks hold on the reference solution, the worker never saw the spec, and the ladder copied impossible checks into ever deeper splits.
Fixes, in the order they landed:

1. **Recovery ladder.** The `no_progress` stuck signal counts only the task's own failures in a row (it counted across tasks, so after one bad task
   every later task escalated after one attempt: 23 of 38 escalations in the rerun). A split child that tests everything its parent tests, or the same
   as an earlier child, is dropped (`splitter.build` compares assertion content; the old check compared command strings, which always differ for
   declarative checks). Splitting stops at `SPLIT_MAX_DEPTH` levels (default 1: a subtask goes to the next rung instead of splitting again; in all real
   runs to date subtasks passed 8 of 83 attempts one level down). The splitter never gives a child an assertion that failed in every attempt at the
   parent (`avoid`).
2. **The worker sees the spec.** `SPEC.md` is in the state directory, which the worker may not read, so its whole picture of a task was a one-line
   title, the frozen checks and the code. The per-item planner now records which requirement each task implements (`spec_items.json`), and the packer
   adds a SPECIFICATION section (priority 1, between the repo map and the task): the spec's introduction plus that requirement. Split children and tasks
   added while a task runs belong to their root's requirement; without a recorded requirement a short spec (up to 3,500 characters) is shown whole.
   The splitter's prompt gets the same text, so its steps stay inside the requirement.
3. **The worker sees its whole check.** The ACCEPTANCE CHECK section showed the check script plus every file it named under one 2,400-character
   cap, and for a declarative check that included the trusted runner's own source next to an indented spec file: on T7 of the control rerun the
   worker saw 1 of its 12 assertions (and spent about 30 turns trying to open the check files it may not read). A declarative check is now shown
   as its command plus one numbered line per assertion; the runner prints `failing assertions: 2, 5` with the same numbers.
4. **Checks grounded in the spec** (`grounding.py`). The spec's own worked examples become assertions the harness writes itself, first in the task
   for the requirement they belong to: "`slugify("Hello World")` is `"hello-world"`" (also returns / are / raises / "(`True`)") becomes a `call`
   (the module comes from "`textutil.py` provides `slugify(...)`" or the spec's only module), "`calc 2 + 3` prints `5`" becomes a `run` of the
   built program after the build command the spec names (`go build -o calc .`). All 59 examples in the catalog hold on the reference solutions.
   Model assertions, in the planner and the splitter, are screened: Python in a project that is not Python, an option, shell syntax or the
   program's own name passed to a program expected to succeed, an exit status `go run` can never report, text the spec sends to standard error
   expected on standard output (not for stdin input or merged streams), a contradiction with a spec example or a finished task's check, and
   assertions of the plan that expect results for the same input that cannot all hold (a strict majority wins; otherwise all of them go) are
   dropped and logged. A check that runs the program the spec says how to build starts with the build step (the binary is git-ignored, so it
   survives a reset and would otherwise be judged stale). A requirement whose per-item replies are both unusable is planned from its examples
   alone (plan.log op `item_from_examples`). Real-model check (2 fresh calc_go plans, control model): 35/45 and 42/45 frozen assertions hold on
   the reference, against 21/41 and 14/51 for the two earlier control builds; 4 of 7 tasks fully sound in each. On the replay corpus (rebuilt with the calc_go
   runs: 718 real assertions, 381 impossible) the screen drops 491 uses of impossible calc_go assertions and keeps 105 of 109 correct ones (the 4
   are `python3 calc.py` checks that "hold" only because Python cannot open the missing file); on level 1 and pyutils it drops 39 impossible uses
   and 4 correct ones, each half of a pair the model contradicted itself with. `tests/test_w_corpus.py` ratchets these numbers per suite.
5. **"Is the check wrong?" before a split** (`rederive.py`). The engine records, per attempt, which assertions of the task's own check failed (the
   runner's numbers). When the ladder reaches the split rung, the assertions that failed in EVERY attempt since the check was set are judged
   against the spec in one model call (the planner's model; "does the specification require exactly this result for exactly this input?") that
   may also propose replacements. The harness decides: only those persistent failures can go, the spec's own examples never do, replacements are
   screened like planned assertions and may not repeat a removed one, and the result must not be broken. The new check is a new frozen spec file
   (`T4.r1.spec.json`, written by `checks.replace_check` together with the lock, the frozen copies and the task's `check_history`; the old file
   stays), the task starts again at rung 1, and the step returns `task_failed` with `check_rederived`. At most `REDERIVE_MAX` times per task
   (default 1, 0 = off); the call is paid from the task's own budget and is skipped when less than 2 minutes of it are left. Every outcome is
   logged (plan.log op `check_rederived`, the reply in `rederive.reply.<task>.json`). When nothing changes, the split goes ahead and no child gets
   a persistent failure. First real run (calc3, 2026-10-17): it removed T1's impossible `go build ... -v` check and kept T3's correct ones, but
   judged all 12 of T7's impossible one-argument checks required (item 7 below now drops those before anything is frozen).

### Fixes after the first build on the fixed harness (2026-10-18)

The control model on the fixed harness (calc3, 2026-10-17): 20/32 hidden tests as left (16/32 counting only verified code) and 11/20 hand inputs,
against 16/32 and 13/32 for the two earlier control builds; 44 of 57 frozen assertions sound. What still went wrong, and the fixes:

6. **`go run` checks run the built program** (`grounding.via_built`). Dropping the checks that expect exit status 2 through `go run` (which reports
   every failure as 1) left the calculator's exit statuses untested, and it exited 1 for every error. When the spec says how to build the program
   with Go, a `go run . ARGS` / `go run main.go ARGS` assertion becomes `./calc ARGS` (after the build step), unless it expects status 1, which
   through `go run` means "failed" with any status. On the corpus 6 impossible checks become sound and none of the 42 rewritten sound ones breaks;
   one (`calc 3 + 3` expecting status 2) becomes a wrong check that is kept instead of dropped.
7. **One word per argument** (`grounding._joined`). All 12 checks of calc3's standard-input task passed the expression as ONE argument
   (`go run . "2 + 3"`, meaning standard input), so the task could never pass. A command expected to succeed that packs into one argument words the
   spec passes as separate arguments (`calc 2 + 3`) is dropped, with a reason that points at the stdin field. Only when every command line of the
   spec gives the program one word per argument and some give it several; a spec that quotes an argument with spaces itself (pyutils) is exempt.
   Corpus (rebuilt with calc3 and the two planning probes: 788 assertions, 410 impossible): drops all 17 such checks and no correct one.
8. **The worker implements the requirement, not the check** (`modes/code/worker.md`). The rule was "make the smallest change that makes the
   ACCEPTANCE CHECK pass", and calc3's code handled exactly the argument counts its checks used (1 or 3 tokens). The rule now asks for general
   code for what the TASK and the SPECIFICATION section ask, never special-casing the check's inputs; the check still decides. Prompt only: the
   next real build measures it.
9. **A blocked or timed-out task leaves only verified code** (`engine._shelve`, `gitops.shelve`). calc3's blocked T7 left a half-done `main.go`
   in the tree, so the program as left was not the verified one. The attempt's changes since the last checkpoint are saved as
   `unverified/<task>.attempt<N>.patch` in the state directory (`git apply` restores them; ignored files such as the built program are not in
   it), the code goes back to the last verified checkpoint, and the blocked question names the patch. A timeout, which already reset the code, now
   saves the patch too. Bench scores "as left" now equal verified-only scores: compare a new build with calc3's 16/32, not 20/32.

Found, not fixed yet: the split children of calc3's precedence task checked the program with `python3 -c "subprocess.run([...], input='2 + 3',
capture_output=True)"`, which raises TypeError on every run (a str input without `text=True`), so they could never pass.

### Fixes after the calc4 build (2026-10-18)

The calc4 build (15/32 hidden, 10/20 hand inputs; calc3's verified code 16/32 and 10/20) showed fixes 6-9 working as built and failed on the
defects below (details in `docs/MODEL_EVAL.md`). The user approved fixing them and one more build to measure them.

10. **A repair reply goes only to a rejected task** (`planner.route_repairs`). The planner answered T7's rejection with `"id": 1`, twice, and the
    replies replaced T1's assertions (the second time with the guide's own `shapes.area` example). A reply now applies only to a rejected task; a
    lone reply to a lone rejection is for that task whatever number it gives; others are logged (`repair_ignored`). The prompt names the numbers.
11. **The planner and the splitter are told how to give standard input, and that standard error is not compared** (`planner.GUIDE`,
    `splitter.TYPES`). The guide listed only the output fields, so the planner wrote `./calc echo "2 + 3"` in all three rounds and the stdin task
    (6 of the 32 hidden tests) was dropped from the plan; it also put the `error:` text the spec sends to standard error in stdout_contains.
    A planning probe with the new guide still had no input: the planner wrote `./calc` expecting "5", without `stdin`. Constrained decoding writes
    required fields before optional ones, so the optional `stdin` could only follow the expected output (`checkspec.assert_schema`); run variants
    with `stdin` required now offer it right after `argv`. The next probe gave the stdin task five stdin checks (3 hold on the reference), but also
    gave the precedence task four with the whole command line as input (`calc 2 + 3 * 4`); a stdin line that starts with the program's own name is
    now dropped (`grounding._named_in_stdin`), as its own name in argv already was.
12. **An assertion that contradicts itself is dropped** (`grounding._contradictory`): exact stdout S plus a stdout_contains text not inside S can
    hold for no program (the runner strips stdout), so the rule is sound by construction. 4 of T6's 6 checks were `"stdout": ""` with
    `"stdout_contains": ["error:"]`. Corpus rebuilt with calc4 (797 assertions): 17 distinct assertions (41 uses) contradict themselves, all
    impossible; the screen now lets through 47 impossible calc_go checks (51 before) and 139 in level_01 (153).
13. **The re-derivation judges a run of the program blind** (`rederive.blind`, `rederive.verdicts`). Shown `calc 2 3` expected to print 5, the
    judge agreed ("a valid expression"); it agreed with all five wrong checks of T6. A persistently failing run of the program is now listed by its
    input only; the judge names the exit status the spec requires, checking the spec's error rules first, and the harness removes a run whose
    expected status differs. Real model, replaying past re-derivations: calc4's `calc 2 3` removed 3/3 (1/2 without the error-rule sentence),
    calc3's correct precedence checks kept 5/5, calc3's `go build -o calc . -v` removed 2/2; calc3 T7's one-argument checks still kept (0/2;
    the screen drops those since fix 7).
14. **A split starts from the last verified checkpoint** (`engine._from_checkpoint`). T6 was split while its last attempt had left a syntax error,
    so a child whose only assertion was the build step failed on that tree, was accepted, and was "verified" by fixing the error (T6.1). After the
    re-derivation, the last attempt is saved as a patch (plan.log `shelve`) and the code reset, so the guard judges children on verified code.
15. **An attempt that passed its own check is where the next one starts** (`engine._keep_near`, `_from_near`). T3's attempt 3 passed all 9 of its
    checks and broke only T2's `6 x 7`; the worker was told "keep the new work", but rung 2 resets the code before every attempt, attempts 4 and 5
    started from scratch (and wrote parsers that never stop), and T3 timed out. Such an attempt is now kept as `unverified/<task>.near.patch`
    (plan.log `near_success`); every later attempt starts from it unless a later one breaks fewer tasks; the split rung is skipped (what is left is
    what it broke); a blocked question names it.

Still open: standard error cannot be checked at all, so the spec's `error:` prefix is untestable (calc4 printed `Error:`: its `calc 5 / 0`
exited 1 as the spec says and still failed the hidden test); a program that never stops costs 60 s per assertion (8 minutes per check run, 16 of T3's 90
minutes); the planner narrowed requirement 1 to "a single-number or two-token expression" in T1's title, and the worker built exactly that.

### Found in the calc5 build (2026-10-18)

calc5 (fixes 10-15) scored 13/32 hidden and 9/20 hand inputs (calc4: 15/32, 10/20); details in `docs/MODEL_EVAL.md`. Not fixed yet:

- **Fix 15 backfired.** T7's attempt 2 passed T7's checks by rewriting `main.go` as a stdin-only program and broke five done tasks; attempts
  3-5 started from that patch, ran T7's check (it passes), and finished without a change. The worker's rules say to finish when its check
  passes, and the regression output in its context did not outweigh that. A near-success should be one that broke little (calc4's broke one
  check of one task), and the worker should see a done task's failing check as a failure of its own check.
- **The worker rewrites whole files, often identically.** `write_file` writes even when nothing changes and answers "wrote main.go (3383
  chars)"; the repeat warning comes at the third copy and is ignored. Same-size rewrites took 46% of the model's time in calc3 and 27% in calc5
  (79 of T3's 87 model minutes were whole-file writes and replies cut off at the token limit).
- **Standard error cannot be checked** (still open from calc4): all 6 failing hidden error tests check that stderr starts with `error:`.
- **Error checks were lost before freezing.** The planner wrote each error input as a pair (`"stdout": "", "exit": 2` and
  `"stdout_contains": ["error:"]`, whose exit defaults to 0); the plan-wide clash rule drops both of a tie, which removed all five inputs. The
  `go run . 10 / 0` exit-1 checks that were left hold on code that does not compile (via_built keeps exit-1 `go run` checks), so the guard
  rejected T6, and the repair copied the guide's new example `["python3","greet.py",""]` twelve times.
- **Shell globbing in the worker's own commands**: `./calc 2 + 3 * 4` passed the file names instead of `*`, and the worker debugged an error
  its code did not have.
- An argument the spec never mentions passed with stdin (`./calc cwd`, 7 of T1's checks) is not screened; a task whose check holds before any
  work (T5, `weak_check`) is accepted even when its title names behaviour the code lacks (`strconv.FormatFloat`).
- Flaky test: `test_v_capbench ... test_oracles_accept_the_reference_solution_and_reject_the_bare_seed` failed once in 12 full-suite runs on
  level_08's `test_r07_too_large_a_body` (a 1.1 MB body the server answers with 413); it passed 3 of 3 on its own. Rerun before suspecting a change.

### Baselines: a plain loop against the pipeline (2026-10-18/19)

Three rounds of fixes moved calc_go 16 -> 15 -> 13/32 and every build found new defects; the user said the path "leads no where". The fixes
above (calc5 findings) were NOT made: we stopped changing `lib/` and measured what had never been measured. Details in `docs/MODEL_EVAL.md`,
"A plain loop without codebot".

- A plain loop (spec in, whole `main.go` out, feedback = compiler output + the 15 spec examples, a fresh prompt each round) is a coin flip per
  run: 4 of 9 runs reached 22-26/32 hidden, 5 stalled at 7-10 (codebot: 16, 15, 13), in 13-35 min against codebot's 2.7-3.9 h.
- Stalled runs send back the identical reply at temperature 0.2 once the prompt stops changing. The loop's own signal (spec examples
  passed) tells good runs from stalled ones, so the best of three runs by it scored 25, 22 and 26.
- `bench/ladder/catalog/calc_go/reference_plan.json` (25ab269): 71 hand-written checks, validated. As the loop's feedback they added 4 where
  the loop moved and nothing where it stalled. `--mode reference-plan` (codebot's pipeline with these checks) has not been run.
- Restart loop (2026-10-19: a fresh draft after 2 rounds without improvement, the best of all drafts by the spec examples delivered, 45 min
  per run, same seeds): 22, 6, 22/32 against 22, 7, 10 without restarts; it rescued one of the two stalled seeds, not the other.
- Ceiling: in all 12 loop runs (59 distinct programs) no version passed more than 13 of the 15 examples; every 13/15 version fails the two
  precedence examples (`2 + 3 * 4`, `2 * 3 + 4 * 5`) and the model sends the same code back when shown them. Neither restarts nor the 71
  checks got past it.
- Runaway replies: 5 of the restart runs' 51 replies ran to the 6000-token cap (12.5 min each, 64 of 145 min), the model reasoning in code
  comments; the two 13/15 replies were 605 and 830 tokens.
- Python levels (2026-10-19, `bare_py.py`: the restart loop writing every module, 45 min, seeds 1-2): level_01 63 and 60/64 hidden
  against codebot's 50, 30, 47 (2026-10-15 run, 1.8 h per trial, before the regression gate); the first reply alone scored 60 and 55.
  level_03: both runs passed all 6 examples and scored 58 and 63/87; the examples cover only 3 of 8 requirements and seed 1's module has no
  `resolve_subset` at all (R8 0/12). 19 of 38 repair rounds sent the program back unchanged; all 4 delivered versions came from a restart.
- The loop scripts, drivers and per-run results are in `bench/baselines/` (see its README). The loop's limit is its signal: where the
  spec's examples do not cover a requirement (level_03), nothing checks it.
- **Decision (user, 2026-10-19: "Let's do it")**: rebuild codebot around the loop. Done as drafts mode (next section); the task pipeline is kept
  unchanged (the user's rule: do not break existing commands; extend before rewriting).

### Drafts mode (2026-10-19)

- `lib/codebot/drafts.py` ports `bench/baselines/bare_py.py` into the harness, generalised to any language with a build command in the spec (Go
  via `go build`, C) and to specs that name no file (the language's default file, e.g. `main.go`). Same model settings as the measured loop
  (temperature 0.2, 6000 output tokens, thinking off, seed `DRAFTS_SEED * 1000 + round`, patience 2). `tests/test_z_drafts.py` holds it to the
  measured loop: the same files, examples, first/repair/system prompts and the same feedback word for word on level_01 (`evaluate` against
  `bare_py.evaluate`), and extraction identical on all 57 recorded replies of the level runs (checked once, not in the suite: the replies are not kept).
- Differences from the baseline loop: the build command and the examples run in the Docker sandbox (Python compiling stays on the host, it runs
  no model code); the time cap counts round time only (pauses do not count); state is saved after every round (resumable); every new best
  version is committed to the code dir; a version that drops a file an earlier best version added puts it back to the baseline.
- Bench: `codebot bench run --level N --mode drafts --task-minutes 45 --seed 1 --repeats 2` (the repeat number picks the seed, so repeats 1-2 are
  the baselines' seeds 1-2). The trial records `drafts` (rounds, drafts, delivered round and examples, uncovered items, cut-off replies, unchanged
  repairs); the classifier adds a VERIFICATION_GAP when every example passed but the hidden tests did not (`classify.RULES_VERSION` 2).
- Real-model check (run `20261019-200101-drafts-l1`, commit 3481000, `codebot bench run --level 1 --mode drafts --task-minutes 45 --repeats 2
  --seed 1`): 63/64 and 60/64, the baseline's numbers, round for round (seed 1: all 21 rounds the same; seed 2: the first 23 of 24, one round
  fewer before the cap because sandbox runs and commits count in a round's time). First level the benchmark reports as held (median 96%).
- Tuned from the 13 recorded runs (MODEL_EVAL "Tuning drafts mode"): `DRAFTS_PATIENCE=1` (a repair that does not improve ends the draft),
  a reply cap of 3x the longest complete reply that built (at least 3000 tokens), and declared Python names (functions with a signature, a
  one-module spec's exceptions) must exist (`drafts.declared` / `drafts.defined`, counted like examples; `check-spec` lists them). Measured
  2026-10-20 (MODEL_EVAL "The tuned drafts mode, measured"): level_01 63 and 61/64, level_03 74 and 44/87, calc_go 6, 21 and 1/32.
- calc_go lost its time to replies that ran to the token cap. Since then (42e6cd9 and the next commit): the first prompt names the files to
  write and asks for no other (asked for "every file", the model wrote README.md and go.mod first); a cut-off block never replaces a complete
  block of the same file; and the reply is streamed and stopped once it repeats itself (`drafts.loop_watch`: its last 8 non-blank lines
  appeared twice before; every recorded runaway was such a loop; `ollama.chat(stop_if=...)`, the only streaming call). Measured 2026-10-20
  (runs `*drafts4*`): calc_go 21, 21 and 18/32, level_01 62 and 62/64, level_03 63 and 61/87. On level_03 the stop also cut good code (the same
  input check at the top of every function: 6 of seed 2's 8 replies), so `loop_watch` now also requires the lines since the block's previous
  copy to be at least 90% lines the reply already wrote (428 recorded replies: 20 of 22 runaways stopped, 2 of 390 normal replies, none of the
  6 cut ones). The feedback now shows a crash's stderr from both ends with paths relative to the code dir (`assertions._err`). Both not yet
  measured end to end.
- The watchdog (`codebot resume-all`, every 5 min) resumed a benchmark run in the second its cancel finished, and the runner it started died
  with the oneshot service, leaving the trial's loop running alone (2026-10-20, stopped by hand). Now `resume --auto` (the watchdog's) never
  resumes a cancelled or inactive run, and a runner started under systemd gets its own transient unit (`runner.detached_cmd`). To stop a
  benchmark for good: `codebot bench cancel <run>`; check `ls bench/results/ladder/*/.active projects/*/.active` afterwards.
- Not done yet: reruns of a finished project (`start` refuses; make a new project); subdirectories (files are flat); requirements without
  examples are only listed, never checked.

### Larger local models (2026-10-20, in progress; `docs/LARGE_MODELS.md`)

- The user asked for a larger MoE model with CPU/GPU splitting, judged on complete tasks under a 3-hour budget per task, baseline included.
- Qwen3-Coder-30B-A3B at UD-IQ3_XXS (`models/gguf/`, 12.85 GB) runs at 11-15 tokens/s (ornith: 8.5) with Ollama's own runner and `--fit`;
  it is imported as `coder30b-iq3` (`bench/bigmodel/Modelfile.coder30b-iq3`). Run it with `codebot bench run ... --model coder30b-iq3
  --num-ctx 16384` (its KV cache at 32K would take 3 GB). It does not fit beside the user's programs: they go to swap while it runs.
- `bin/codebot` starts Ollama with `LLAMA_ARG_CACHE_RAM=0` (llama-server's 8 GiB prompt cache went to swap). Restarting the private Ollama
  is enough to apply it. The upstream llama.cpp build b11755 (deleted 2026-10-21 with the downloaded GGUF and the 11 evaluation models, 67 GB;
  re-download to rerun) had an expert cache that made this GPU slower.
- Measured under a 3-hour budget per task: calc_go 26 and 26/32 (all 15 examples, in 34 and 28 min) against ornith's 23 and 21 (never
  all 15), level_03 75/87 in 4 min against 64/87 in 64 min. The user then switched this machine to it (2026-10-20): `config/local.env` has
  `CODEBOT_MODEL="coder30b-iq3"` and `NUM_CTX=16384` (delete those two lines to go back to ornith). opencode paths keep ornith. The
  preflight's num_ctx check now compares NUM_CTX with the Modelfile only when codebot's model is `codebot-model`.

## 7. Working agreements and gotchas (learned the hard way)

- **The user cares about honesty**: say what is untested, what failed, and what you got wrong. They explicitly thanked the "nothing ran against the real model yet" disclosure.
  Real-model runs found bugs 238 passing tests did not; always run against the real model before claiming something works.
- **Heat**: the user said the LLM was "overheating my pc" and asked to stop it. Do not leave the model running idle (`ollama stop codebot-model`, keep-alive is 30 min),
  and check `sensors | grep Package`. The thermal guard (`THERMAL_GUARD=1`, pause at 92 C, resume at 84 C) wraps model calls made by the engine (not opencode planner calls).
- The user sometimes pastes advice from another assistant; evaluate it against evidence (e.g. `num_thread 4` was already the default). Their pasted messages may be cut off.
- User prefers short status updates with real numbers, and 2-3 line summaries after each step. Ask before destructive actions or touching anything outside the codebot dir
  (Docker sandbox uses an existing local image and creates nothing; worktrees live in `workspaces/`).
- **Never `pkill -f <pattern>` where the pattern appears in your own command line**: it killed the assistant's shell twice. Use `pgrep -f '[x]pattern'` and kill PIDs.
- Do not poll in a sleep loop; use `Monitor` (max 30 min per monitor) or background commands. `sleep N` chained before other commands is blocked.
- opencode pitfalls: `"patch": false` kills write+edit; `opencode run --auto` auto-approves external dirs; the lean agent is `codebot`/`codebot-re` in `config/opencode*.json`.
- Bash `source project.env` and python `read_env` both read `project.env`; keep it `KEY="value"` one per line. Bench projects set `NTFY_ENABLED=0` so they never push to the phone.
- A code dir must never be inside the state dir (`engine.step` refuses; `codebot start` migrates old worktrees to `workspaces/`).
- Docker sandbox: no `--init` (hidden by the read-only `/usr` mount); `bwrap` does NOT work on this machine (AppArmor blocks unprivileged user namespaces).
- `prompt_eval_count` from this Ollama is the TOTAL prompt size; reuse shows in `prompt_eval_duration`. Replies cut by `num_predict` are `done_reason: length` (kind `truncated`).
- tmux is installed and was used to drive the opencode TUI for planner tests.

## 8. State of the working tree and the machine when this was written

- Ladder smoke run `20261015-152626-smoke1` is kept in `bench/results/ladder/` with its trial project `cap-20261015-152626-smoke1-l01-r1`.
- Nothing is running: no loops, no benchmark, model unloaded (`ollama ps` empty), no `.active` project flags, no containers. NVIDIA 595-open driver loaded (`nvidia-smi` works).
- Bench projects from today's runs are kept under `projects/` (`bench-hard-1/2`, `bench-auto-3..6`; only `bench-auto-6` has a usable plan) and can be deleted. `bench-auto-3/4/5` hold real planner outputs worth keeping as examples of the mistakes the guard now catches.

## 9. Questions still open for the user

Run level 1 at 90 min x3 overnight? Delete the old `bench-*`, `cmp*` and `cap-*` projects? Delete or re-plan `re1`? Switch `SANDBOX=auto` to `docker`? Add `codebot plan-auto`? Cooling pad or repaste? Should the interactive planner also move to declarative checks?
