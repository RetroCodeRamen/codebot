# codebot GUI: everything it must show and control (2026-10-21)

A checklist for designing a graphical interface without losing a feature. Every item says **what to show**, **what to control**, and
**where the data comes from** (a `codebot` command or a file), with a priority: **[1]** first version, **[2]** next, **[3]** advanced or
legacy. Today's interfaces are the command line (`codebot help`) and a terminal menu (`codebot` with no arguments, `bin/codebot-ui`); the GUI
must cover both and must not break either (the user's rule: extend, don't break existing commands).

## 1. What the user manages

| thing | what it is | where it lives |
|---|---|---|
| **Project** | one program to build: a name, a spec, a code folder, settings, a run | `projects/<name>/` (state), the code folder (the user's git repo) |
| **Workflow** | how a project is built: **drafts** (`codebot new`, recommended: the model writes the whole program, the spec's examples judge it) or **tasks** (`codebot plan`, legacy: planned tasks with frozen checks) | `WORKFLOW="drafts"` in `projects/<name>/project.env` |
| **Mode** | what kind of work: `code` (default) or `re` (reverse engineering, read-only targets) | `modes/<mode>/`, `MODE=` in project.env |
| **Run** | a project's loop working unattended: running, paused, stopped, finished, waiting for an answer | `loop.pid`, `PAUSE`, `STATUS`, `drafts/state.json` or `tasks.json` |
| **Model server** | the private Ollama (port 11500), its models, the loaded one | `tools/ollama/`, `models/`, `config/local.env` |
| **Machine** | heat, memory and swap, GPU memory, disk, Docker sandbox | `/proc`, `sensors`, `nvidia-smi`, `thermal.jsonl` |
| **Benchmark** | runs of the Capability Ladder (12 levels, calc_go, a growing project) scored by hidden tests | `bench/results/ladder/<run>/` |
| **Settings** | about 70 knobs: shipped defaults, this machine's overrides, per-project overrides | `config/codebot.env`, `config/local.env`, `projects/<p>/project.env` |
| **Notifications** | ntfy pushes to a phone, and optional phone commands back | `NTFY_*`, `PHONE_CONTROL` |
| **Memory** | lessons the task pipeline learned, the user's preferences | `memory/` |

**Rule the GUI must enforce: one model job at a time.** A project run, a benchmark, a preflight or an evaluation each load the model; two at
once overheat the laptop and overflow memory. Show what is running in a global bar and block or warn before a second model job starts.

## 2. Screens

1. **Home**: projects at a glance, what is running now, machine health, anything waiting for the user (a question, a finished run).
2. **Projects**: the list (drafts and tasks; benchmark trial projects `cap-*` hidden behind a toggle).
3. **New project** (drafts wizard): name, spec, code folder, coverage check, settings, start.
4. **Spec editor**: requirements, examples, live preview of the checks codebot will run.
5. **Project (drafts)**: status, controls, rounds timeline, best version, code diff, report, audit, log.
6. **Project (tasks, legacy)**: plan, tasks tree, questions and answers, checks, progress.
7. **Live view**: the log and state changes of a running project (today's "attach").
8. **Benchmarks**: levels, start a run, runs and trials, reports, history, validation.
9. **Models**: server, models, default model, context, preflight, load and unload.
10. **Machine**: heat and the thermal guard, memory and swap, GPU, disk, sandbox, watchdog, doctor.
11. **Notifications**: ntfy and phone control.
12. **Settings**: every knob, with where its value comes from.
13. **Memory** (legacy): expertise, lessons, tidy.

## 3. Feature inventory

### 3.1 Projects list [1]
- **Show per project:** name; workflow (drafts or tasks); mode (code or RE); state (running / paused / stopped / finished / blocked: waiting
  for an answer); progress (drafts: round, draft, best examples `x/N`, minutes used of `DRAFTS_MINUTES`; tasks: done / left / blocked); the
  last status message (`STATUS`); the code folder; when it started (`started.at`).
- **Controls:** open; start; pause (now, or `--soft` after the current step); resume; stop; new run of the same spec with another seed (a
  finished drafts project cannot be re-run: it is a new project); delete or archive (**no command exists yet**, see section 5).
- **Source:** `codebot list` / `codebot status [name]` (text), `projects/*/project.env`, `loop.pid` (alive = running), `PAUSE`, `STATUS`,
  `drafts/state.json` (`round`, `draft`, `best`, `finished`), `tasks.json` (statuses), `bin/codebot-ui` `info()` shows how to derive them.

### 3.2 New project, drafts [1]
- **Fields:** name (letters, digits, `.`, `_`, `-`; must not exist); spec (pick a file, or write one in the spec editor); code folder (must be
  outside codebot's own folder; created if missing; a git repo is recommended: codebot then works on branch `codebot/<name>` and never touches
  the user's checkout); optional per-project settings (time budget `DRAFTS_MINUTES`, seed `DRAFTS_SEED`, model).
- **Show before creating:** `codebot check-spec SPEC.md [dir]`: requirements and how many runnable examples each has, the ones with **no**
  example (nothing will check them, so highlight them), the language and build command it detected, whether spec plus code fit drafts mode.
- **Controls:** create (`codebot new <name> <spec> <dir>`), create and start, cancel.

### 3.3 Spec editor [2]
The examples in a spec are the only thing drafts mode can check, so this screen is where results are won or lost.
- **Show:** the numbered requirements; per requirement the examples codebot recognises, as a table (call or command, input, expected output,
  standard error, exit status, standard input); requirements with none; the language and build command.
- **Helpers:** insert an example of each recognised kind: `` `f(x)` is `y` `` (also returns / gives / equals), `` `f(x)` raises `ValueError` ``,
  `` `calc 2 + 3` prints `5` ``, `` ... prints `msg` to standard error ``, `` ... prints a message starting with `error:` ``,
  `` ... nothing to standard output ``, `` ... exits with status 1 ``, `` `calc` with standard input `2 + 3\n1 / 0` prints `5\nerror: ...` ``
  (`\n` = line break). A Python call example needs the function declared with its module (`` `textutil.py` provides `slugify(text)` ``).
- **Validate:** live preview through `codebot check-spec` (or `drafts.coverage()` / `drafts.examples()` in `lib/codebot/drafts.py`).
- A spec can only be edited before its project starts (after that: a new project).

### 3.4 Project, drafts [1]
- **Header:** state; round and draft; best version (`x/N` examples, `y/M` declared names for Python); minutes used / budget; stop reason
  when finished (all examples pass / time cap / round cap / model errors); delivered commit on `codebot/<name>`.
- **Controls:** start, pause, resume, stop; give it more time (raise `DRAFTS_MINUTES` in project.env: read every round, applies at once);
  run again with another seed (new project); open the code folder; show the diff (`codebot diff <name>`, with the merge instructions it prints);
  report (`codebot report <name>`, also `--short`, `--json`, `--send`); audit (`codebot audit <name> [--probe]`: what ran, proof of offline).
- **Rounds timeline** (`drafts/rounds.jsonl`, one row per round): round, draft, fresh draft or repair, seconds, thermal wait, prompt and
  generated tokens, output cap, how the reply ended (`stop`, `length` = cut off, `loop` = stopped for repeating itself), files written, built?,
  examples passed, declared names, improved?, unchanged (repair returned the same code)?, restart next?. Chart: examples passed per round,
  best so far as a line, drafts as bands.
- **Round detail:** the exact prompt (`drafts/rounds/NN/prompt.md`), the reply (`reply.md`), the code (`rounds/NN/code/`), and the feedback
  it produced (the next repair prompt's last section: build errors with their lines, failing examples).
- **Best version:** its files, which examples it fails and why (the feedback text in `drafts/state.json` `best.feedback`).
- **Summary** (`drafts/summary.json`): rounds, drafts, cut-off and looped replies, unchanged repairs, model seconds, thermal seconds, tokens.

### 3.5 Live view [1]
- **Show:** the log as it grows (`loop.log`); one-line state changes (state, round, best); thermal pauses (`thermal.jsonl`); the model
  generating (the runner is busy: `ollama ps` shows the loaded model).
- **Controls:** pause/resume, stop, answer (tasks), detach (the run keeps going).

### 3.6 Project, tasks pipeline [3] (legacy, kept working)
- **Plan:** `PLAN.md`, the planning chat (`codebot plan`: an opencode terminal interview, `--quick` or the thorough "grill me" style;
  open it in a terminal rather than re-implementing it), `codebot check-plan` (tests every check against the code now), `codebot freeze`
  (`--allow-unchecked`, `--allow-passing-checks`).
- **Tasks tree** (`tasks.json`): id, title, status (pending, running, done, blocked, skipped, timeout), size, deps, files, attempts, ladder
  rung, commit, parent and subtasks, the frozen check and its assertions (`checks/`), time used (`taskclock.json`).
- **Blocked tasks:** the exact question and an answer box (`codebot answer <name> "T3: guidance"`; resets the task's ladder); past answers
  (`ANSWERS.md`). `PROGRESS.md`, `attempts.jsonl`, `trace.jsonl`, `stats.json` for detail.
- **Prompts:** `codebot prompt <name> [plan|work] [quick]` prints exactly what the model receives.

### 3.7 Questions and alerts [1]
- One inbox for everything waiting for the user: blocked-task questions (tasks pipeline), finished runs, failed runs, benchmark completion.

### 3.8 Benchmarks [2]
- **Levels:** `codebot bench list` (12 levels + calc_go + the growing project; title, language, requirements).
- **Start a run:** `codebot bench run` with `--level N` / `--levels 1-3` / `--bench calc_go` / `--suite ladder|growing`, `--repeats`,
  `--mode full|reference-plan|minimal|drafts`, `--task-minutes`, `--seed`, `--model`, `--num-ctx`, roles (`--planner-model`,
  `--planner-think`, `--splitter-model`, `--splitter-think`, `--worker-think`, `--escalate-model`), `--interactive`, `--checkpoints`,
  `--order`, `--driver`, `--no-notify`, `--name`, `--dry-run`.
- **Runs:** status (`bench status [run]`), trials with outcome, spec examples, hidden tests passed / total and by requirement, minutes, heat
  (`trials/*/trial.json`), report (`bench report [run]`, `report.md`), history (`bench report --history`), resume, cancel.
- **Also:** `bench validate` (the benchmark's own oracle self-test, no model), `bench --preflight`, `bench --thermal` (speed versus heat
  sweep), progress at fixed minutes (`bench/bigmodel/checkpoints.py RUN --minutes 90,150,180`).

### 3.9 Models [2]
- **Show:** the private Ollama (up/down, version, port); models (name, size, family, quantization, context: `ollama list` / `/api/tags`);
  the loaded model (memory on GPU and in RAM, when it unloads: `/api/ps`); the default model (`CODEBOT_MODEL`) and its context (`NUM_CTX`),
  `KEEP_ALIVE`; the last preflight result.
- **Controls:** start the server (`codebot setup`); set the default model (writes `config/local.env`); preflight (`codebot bench
  --preflight`: context, keep-alive, JSON schema, KV-cache reuse); unload now; import a model file (`ollama create` from a Modelfile, see
  `bench/bigmodel/Modelfile.coder30b-iq3`); delete a model (confirm). Model evaluation (`codebot eval mutants|gate|planner`) is [3].

### 3.10 Machine and safety [1 for the top bar, 2 for the screen]
- **Heat:** CPU package temperature now and as a graph; thermal guard on/off, pause at `THERMAL_MAX` (92 C), resume at `THERMAL_RESUME`
  (84 C); pauses so far.
- **Memory:** RAM available, swap used, and while a large model runs, swap traffic: the 30B model pushes the user's programs to swap.
- **GPU:** memory used by the model. **Disk:** models, projects, work folders (and a cleanup action, confirm each).
- **Sandbox:** `SANDBOX` (docker / auto / none), the image present?, memory cap; the audit's network probe.
- **Watchdog:** the systemd timer installed? (`codebot install-service`); it restarts runs whose loop died (`codebot resume-all`).
- **Doctor:** `codebot doctor` (prerequisites, Ollama, model, sandbox image, notification settings), shown as a checklist.

### 3.11 Notifications and phone [2]
- `NTFY_ENABLED`, `NTFY_SERVER`, `NTFY_TOPIC` (a secret: anyone with it can read and send; mask it, never show it in screenshots or logs),
  `NTFY_TOKEN` (secret), `NOTIFY_EACH_TASK`; send a test (`codebot notify-test`).
- `PHONE_CONTROL`: the phone can send pause / resume / stop / status / an answer to `<topic>-reply`; show that it is on, because it is a
  remote control.

### 3.12 Memory [3]
- `memory/expertise.md` (the user's preferences: an editor), `memory/lessons.md` (generated, read-only), each lesson's use and pass rate
  (`lessons.status.json`), `codebot tidy` (prune lessons that go with failure).

### 3.13 Settings [2]
Show every knob with its value **and where the value comes from**: shipped default (`config/codebot.env`), this machine
(`config/local.env`), or this project (`projects/<p>/project.env`); allow overriding at each level and resetting. Files are `KEY="value"`, one
per line; both bash and Python read them, so the GUI must write that format exactly.

| group | knobs (default) |
|---|---|
| model and server | `CODEBOT_MODEL` (codebot-model; this machine: coder30b-iq3), `OLLAMA_PORT` (11500), `NUM_CTX` (32768; 16384 here), `KEEP_ALIVE` (30m), `THINK` (off) |
| drafts | `DRAFTS_MINUTES` (60), `DRAFTS_PATIENCE` (1), `DRAFTS_MAX_ROUNDS` (60), `DRAFTS_NUM_PREDICT` (6000), `DRAFTS_TEMPERATURE` (0.2), `DRAFTS_SEED` (1) |
| tasks pipeline | `WORKER` (native), `CONTEXT_BUDGET` (8000), `WORKER_MAX_TURNS` (24), `WORKER_CONTEXT_TOKENS` (22000), `WORKER_TEMPERATURE` (0.2), `WORKER_MAX_TOKENS`, `RUN_TIMEOUT` (120 s), `LADDER_TRIES` ("2,2,0,2,1"), `SPLIT_MAX_DEPTH` (1), `REDERIVE_MAX` (1), `ALT_TEMPERATURE` (0.8), `ESCALATE_MODEL`, `TASK_MAX_ATTEMPTS` (8), `STUCK_SAME_FAILURE` (3), `STUCK_NO_PROGRESS` (6), `PINGPONG_WINDOW` (6), `PROJECT_MAX_MINUTES` (0 = none), `CHECK_BASELINE_GUARD` (1), `REGRESSION_GATE` (1), `PLAN_PER_ITEM` (1), `PLAN_STYLE` (grill), `CHECK_TIMEOUT`, `ALLOW_UNCHECKED` |
| roles | `PLANNER_MODEL`, `PLANNER_THINK`, `SPLITTER_MODEL`, `SPLITTER_THINK`, `WORKER_THINK` (off / on / low / medium / high), `THINK_BUDGET` |
| legacy opencode loop | `ITER_TIMEOUT` (2700 s), `MAX_ITERATIONS` (300), `STALL_LIMIT` (3), `LESSONS_MAX` (4000) |
| sandbox | `SANDBOX` (auto), `SANDBOX_IMAGE` (ubuntu:24.04, never pulled), `SANDBOX_MEMORY` (2g), `SANDBOX_PIDS`, `SANDBOX_RO`, `SANDBOX_PASS_ENV` |
| heat | `THERMAL_GUARD` (1), `THERMAL_MAX` (92), `THERMAL_RESUME` (84), `THERMAL_MAX_WAIT` (1800 s) |
| notifications | `NTFY_ENABLED` (0), `NTFY_SERVER`, `NTFY_TOPIC`, `NTFY_TOKEN`, `PHONE_CONTROL` (0), `NOTIFY_EACH_TASK`, `IDLE_POLL` (15 s) |
| for testing only | `WORKER_CMD`, `SPLIT_CMD`, `SPLITTER` (hide or put behind an "expert" switch) |

`LLAMA_ARG_CACHE_RAM=0` is set by `bin/codebot` for the model server, not a user knob. Some settings must stay consistent: `NUM_CTX` with
the model (the preflight checks it).

## 4. Global elements
- **Top bar [1]:** model server up/down; the loaded model; CPU temperature with the guard's state; memory and swap pressure; the one model
  job that is running (project, benchmark or check), with a link to it; notifications on/off.
- **Confirmations [1]:** stop a run, cancel a benchmark, delete or archive a project, delete a model, change the default model while
  something runs, turn on phone control, any disk cleanup. Pausing frees the model's memory; stopping ends the run.
- **Long operations:** builds and benchmarks run for minutes to hours and keep going when the GUI closes; the GUI only watches them.

## 5. What the backend does not offer yet (the GUI will need these)
1. **Structured status:** `codebot status` and `list` print text; a `--json` form (as `report --json` already has) or a small local API.
2. **Delete, archive or rename a project** (the state folder, the work folder in `workspaces/`, the branch and its worktree): no command.
3. **Per-example results of the best version** as data (today: the feedback text).
4. **Live updates:** today only files to watch (`loop.log`, `audit.jsonl`, `rounds.jsonl`, `thermal.jsonl`); an event stream would be simpler.
5. **Model management:** import and delete are Ollama commands, not `codebot` commands.
6. **Machine metrics** (memory, swap, GPU, disk) are not exposed by `codebot`.
7. **Settings writes:** no command; the GUI writes the env files (keep the `KEY="value"` format and the comments).
8. **The planning chat** is a terminal program (opencode): open a terminal for it.

## 6. Plan
1. **Decide the shape.** A web page served by codebot on `127.0.0.1` only fits the local-only rule and works from any browser on the
   machine; a desktop app (Qt or Tk) is the alternative. Either way, a thin local layer that runs `codebot` commands and reads the files
   above, so the GUI, the menu and the command line can never disagree (the rule `bin/codebot-ui`'s benchmark menu already follows).
2. **First version [1]:** top bar; home; projects list; new drafts project with the coverage check; drafts project screen (status,
   controls, rounds timeline, best version, diff, report); live view; confirmations. Backend: items 1, 3, 4 of section 5.
3. **Second [2]:** spec editor with the example helper and live preview; models; machine screen; notifications; settings with
   scopes; benchmarks (start, runs, reports).
4. **Third [3]:** the tasks pipeline screens, RE mode, model evaluation, thermal sweep, memory.
5. **For each screen, design these states:** empty (no projects yet), loading, running, paused, finished (success / time cap / failed),
   waiting for an answer, model server down, sandbox missing, too hot (paused by the thermal guard), and the "another model job is
   running" block.
