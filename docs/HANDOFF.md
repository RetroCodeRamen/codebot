# codebot: handoff (read this first in a new session)

Last updated: 2026-10-15, after the declarative-checks redesign and its first real-model run. Everything described here is committed (`git log` in this repo); the full suite (333 tests) was green at the last engine change.
This file is the single place that says what the project is, what state it is in, what is in flight, and what to do next. `docs/ROADMAP.md` has
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

Repo: `/home/aj/Documents/Development/codebot` (git, branch with ~27 commits; `projects/ models/ tools/ logs/ workspaces/ bench/runs/ bench/results/` are git-ignored).

| path | what |
|---|---|
| `bin/codebot` | bash CLI + supervisor loops (legacy loop and `loop_engine`), notify/ntfy, phone control, watchdog install. Dispatch is the `case` at the bottom. |
| `bin/codebot-engine` | python CLI over the engine: freeze, step, status, render (re-render PLAN.md from tasks.json), answer, audit, report, check-plan, modes, mode-info, prompt-template, sizing, split, memory, plan-op, reset |
| `bin/codebot-ui` | the interactive menu (`codebot` with no args) |
| `bin/codebot-bench` | `codebot bench ...` (preflight, suites, thermal sweep; the subcommands `list/run/status/report/resume/cancel/validate/coverage` go to `lib/codebot/capbench/`, see `docs/BENCHMARK.md`) |
| `bin/codebot-mkconfig` | builds `config/opencode-re.json` (REA MCP config; data named in `modes/re/mode.json`) |
| `lib/codebot/*.py` | the engine (python stdlib only): `engine` (step state machine), `tasks` (tasks.json store + integrity), `checks` (freeze/lock/tamper/run), `guard` (baseline guard), `planparse`, `planops` (add/split/reorder only), `gitops` (baseline commit, checkpoints, reset), `budgets` (stuck signals), `ladder`, `sizing` (split + stats), `splitter` (model-driven task splitter, the default SPLIT_CMD with the native worker; emits declarative assertions), `assertions` (declarative checks: the trusted assertion runner, copied into every project's `checks/` as `assert_runner.py`; exit 0 holds / 1 real failure / 2 unusable spec), `checkspec` (assertion JSON schema, spec files, check command, PLAN.md rendering), `planner` (native unattended planner: one constrained call + harness-driven repair rounds, used by `codebot bench --plan auto`), `worker` (native worker), `packer` (context), `ollama` (client), `tokens` (calibrated estimate), `thermal`, `taskclock` (per-task wall-clock budgets: one persisted budget per root task, enforced by killing the worker's process group; see ROADMAP), `proc` (run a shell command and kill its whole process tree on timeout), `sandbox` (Docker + policy), `audit` (hash chain), `memory` (evidence-backed lessons), `modes`, `report`, `preflight`, `bench`, `thermalbench`, `config` |
| `lib/codebot/capbench/` + `bench/ladder/catalog/` | Capability Ladder Benchmark (12 levels + growing notes project, hidden reference tests, per-task 90 min budget via `taskclock` setting `TASK_MAX_MINUTES`, resumable runs in `bench/results/ladder/`). Modules: `manifests`, `reference`/`refrun` (hidden-test oracle), `workspace`, `runner`, `results`, `metrics`, `classify`, `reports`, `notify`, `cli`. Tests `tests/test_v_capbench.py`, `tests/test_u_taskclock.py`. Docs: `docs/BENCHMARK.md` |
| `modes/<name>/` | `code` (default) and `re` (reverse engineering with REA, `extends` code). `mode.json` + prompts (`plan.md`, `plan-quick.md`, `plan-auto.md`, `plan-repair.md`, `work.md`) + `worker.md` + notes + `verifiers/` |
| `config/codebot.env` | all settings (documented inline). `config/Modelfile` (num_ctx 32768), `config/opencode.json`, `config/opencode-re.json` (generated) |
| `memory/` | `expertise.md` (user-edited), `lessons.raw.jsonl` (append-only), `lessons.status.json`, generated `lessons.md`, `sizing.json` |
| `tests/` | 400 test methods (`tests/run.sh [file]`, about 2.5 min for all, almost no CPU). **Verification status: the last full green run was 284 tests, before the documentation pass; `tests/test_r_docs.py` (16 docs-vs-code tests, added in that pass) was run on its own and passes; the full suite has NOT been re-run since the help text (`bin/codebot` usage heredoc) and `config/codebot.env` comments were edited. Re-run `tests/run.sh` first thing.** `fake_ollama.py` is the scripted fake model server. Files `test_a_...` to `test_q_...` follow the roadmap steps; `test_p_regressions.py` holds bugs found by real-model runs |
| `bench/suite/pyutils` (easy, 5 tasks) and `bench/suite/pyhard` (hard, 5 tasks, one needs a human) | each has `spec.md`, `seed/`, `checks/`, `plan.md`, `reference/` (proven-correct solution used by tests) |
| `projects/<name>/` | per-project state (see README "Per-project files"). `workspaces/<name>/` = git worktrees (code dirs must NOT be inside the state dir) |

Models: `codebot-model` (ornith:9b, default) and `qwen-coder-model` (qwen2.5-coder:7b, for comparison only; `config/Modelfile.qwen-coder`). `codebot bench --model <name>` runs a whole bench with one model; `codebot bench --compare <run> <run>` prints a side-by-side table; `--plan-only` samples a planner in about 3 minutes. Results so far: ornith is the better worker (5/5 in 1 attempt each vs 4/5 with 2 attempts for qwen on the same plan; qwen is 18% faster per token but needs 4.6x the turns) and, with the revised prompt, both models write mostly-but-not-fully sound plans. See `docs/ROADMAP.md`, section "Model comparison".

Environment facts: Acer Predator PT314-51s (Triton 300 SE), i7-11375H (4 cores / 8 threads), 15 GB RAM + 16 GB swap (heavily used), RTX 3050 Ti 4 GB with the **NVIDIA 595-open driver loaded** (about 35% of the model's layers are offloaded automatically; forcing `num_gpu=99` runs out of VRAM), Ubuntu 24.04 / KDE. Private Ollama 0.40.3 binary in `tools/ollama`, models in `models/`, port
**11500** (the system Ollama 0.22 on :11434 is too old for ornith; leave it alone). Model `codebot-model` = `ornith:9b` (Q4_K_M, 5.6 GB, architecture `qwen35`, a
hybrid/recurrent model) + 32k context. opencode 1.19.0 lives in `tools/node_modules`. Docker works for this user (group `docker`); sandbox image `ubuntu:24.04` is
already local. REA: `rea` 6.4.0 (`~/.local/node/bin/rea`), Ghidra 12.1.4 (`~/.local/ghidra_12.1.4_PUBLIC`), JDK 21 (`~/.local/jdk-21`). ntfy topic `codebot-tahlokffwnfdc7`
(reply topic `...-reply`). A systemd **user** timer `codebot.timer` restarts loops that die (only projects with a `.active` file). `codebot` is symlinked into `~/.local/bin`.

## 3. How a run works (engine mode), in one paragraph

`codebot plan` (interactive "grill me" planner via opencode TUI) writes `PLAN.md` where every task has `check:` / `files:` / `size:`. `codebot start` freezes it:
the **baseline guard** rejects checks that already pass, are broken, or have prose after the backticks; checks and fixtures are sha256-locked; `tasks.json` is
harness-owned. The loop calls `codebot-engine step` repeatedly: pre-run the check, run the **native worker** (JSON-schema actions, edits validated, context packed by
the harness, commands in an offline Docker sandbox), run the frozen check, checkpoint in git on pass, otherwise climb the **ladder** (retry, reset+retry, split, alternate
prompt, optional larger model, blocked + a pushed question). Budgets, stuck signals, an audit log (`codebot audit --probe`), evidence-backed memory, thermal guard,
morning report (`codebot report`). Projects without `tasks.json` still run on the original opencode loop.

## 4. Status of the roadmap (`docs/ROADMAP.md`)

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

Done since the last handoff (details and numbers in `docs/ROADMAP.md`): NVIDIA driver installed and offload measured (thermal sweep, auto-fit puts about 35% of the layers on the 4 GB GPU: 3.4x the throughput of CPU-only, still 90+ C); hard suite run twice with the real model (4/5 verified, T5 blocked as designed, all ladder steps seen); the splitter bug found there fixed (guard on split children, children inherit `files`); the free-form opencode planner found unusable after three real runs and replaced by **declarative checks** (planner and splitter emit assertion data, the harness owns the runner, paths and check commands, exit status 0/1/2 is a contract the guard enforces). First real run of the new planner (`bench-auto-6`): plan in 178 s with 0 rejected checks, 5/5 tasks verified, 1 attempt each, 6.7 min.

1. **Harder real-model evidence for the new planner.** `bench-auto-6` used the easy suite: no ladder step fired. Needed: a hard-suite spec run through `--plan auto` (needs a `spec.md` for `bench/suite/pyhard`, which has only a fixed plan today), and a few repeated runs to see how often the planner needs repair rounds and writes weak or unsatisfiable assertions.
2. **Splitter with declarative assertions has only been tested with the fake model** (`tests/test_s_declarative.py`, `tests/test_h_worker.py`). It needs a real run where a task actually splits (the hard suite does this).
3. **Interactive grill-me planner** (`codebot plan demo ~/scratch`) still uses the free-form format with raw shell checks (protected by the guard's static checks). Decide whether it should also produce assertions; not live-tested end to end with the `check:` format.
4. **No user-facing command for the native planner** yet: it is reachable through `codebot bench --plan auto` only. A `codebot plan-auto <name>` that reads a spec file is the obvious next step.
5. **Assertion types may need growing** (for example: run a command and compare to a file, import-time side effects, exit status only). Measure how often the planner needs a raw shell check before adding types.
6. Loose ends: `bench-fixed-2` kept as a reference result (can be deleted); the legacy RE project `re1` is stopped (delete or re-plan in engine mode); `SANDBOX=auto` could become `docker`; the laptop still runs at 90-96 C under load (power-saver gives 65 C at 54% speed; a cooling pad or repaste is the real fix).

## 6. Next steps, in order (copy-paste)

```
cd /home/aj/Documents/Development/codebot
codebot setup                      # starts the private Ollama (the model is unloaded after a reboot; the first request reloads it)
tests/run.sh                       # full suite, about 2.5 min
codebot bench --preflight          # about 5 min, must say PREFLIGHT OK
nohup codebot bench --suite pyutils --plan auto --name bench-auto-N --max-minutes 120 --plan-minutes 40 > /tmp/b-auto.out 2>&1 &   # about 10 min with the native planner
nohup codebot bench --suite pyhard  --plan fixed --name bench-hard-N --max-minutes 90 > /tmp/b-hard.out 2>&1 &                    # about 70 min; one model job at a time, never both
```
Run model jobs **one at a time** and watch `sensors | grep Package`. Use a `Monitor` that reports only state changes (tasks.json statuses), not per-turn noise. After each run: put the results in `docs/ROADMAP.md`, commit, and tell the user what was found including what failed. Do not edit `lib/` while a bench is running (every engine step imports it fresh).

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

- Nothing is running: no loops, no benchmark, model unloaded (`ollama ps` empty), no `.active` project flags, no containers. NVIDIA 595-open driver loaded (`nvidia-smi` works).
- Bench projects from today's runs are kept under `projects/` (`bench-hard-1/2`, `bench-auto-3..6`; only `bench-auto-6` has a usable plan) and can be deleted. `bench-auto-3/4/5` hold real planner outputs worth keeping as examples of the mistakes the guard now catches.

## 9. Questions still open for the user

Delete or re-plan `re1`? Switch `SANDBOX=auto` to `docker`? Add `codebot plan-auto`? Cooling pad or repaste? Should the interactive planner also move to declarative checks?
