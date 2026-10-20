# Model evaluation

How codebot decides which local model(s) to use. The goal is not the highest benchmark score: it is the model or combination that lets codebot finish
the most correct, verified software unattended on this machine (i7-11375H, 15 GB RAM, RTX 3050 Ti with 4 GB VRAM, thermally limited). Concluding
that the current model is already the best option is an acceptable result.

Labels used below: **[V]** verified on this machine or in our data, **[R]** reported by a vendor or third party, not checked by us, **[H]** hypothesis.

## Three separate questions

1. **Planning**: can the model read a spec and write sound, thorough acceptance tests? Measured with the planner probe (no worker involved).
2. **Implementation**: given correct tests, can the model write working code within the per-task budget? Measured with `--mode reference-plan`
   (every worker gets the same validated plan and checks).
3. **Integration**: does a better planner make the same worker finish more correct work per hour, and is a split (big planner, fast worker) or
   escalation (ladder rung 5, `ESCALATE_MODEL`) worth it? Measured last, one variable at a time.

Harness changes are evaluated separately from model changes: on the replay corpus (`tests/test_w_corpus.py`) and with the control model only.

## Scores

**Planner** (per plan, judged against the level's reference solution; `lib/codebot/evalkit/planscore.py`):

| metric | meaning |
|---|---|
| E, executability | share of tasks with no impossible assertion. Primary: one assertion that fails on correct code dooms its task |
| S, soundness | share of assertions that hold on the reference solution |
| K, coverage | per requirement, the share of its focused mutants the plan's sound assertions reject; mean over requirements |
| Q, quality | E x K |
| also | vacuous assertions (hold on the seed), duplicates, invented exceptions, tasks with no behaviour test, unsound assertions by kind |

Mutants (`lib/codebot/evalkit/mutants.py`, stored as `bench/ladder/catalog/<level>/mutants.json`) are copies of the reference solution with one
small bug each. Only mutants the hidden tests reject are kept (so each is a real behaviour change), and only *focused* ones (breaking at most 2
requirements) count for K; mutants of shared core code are caught by almost any test and are reported as `K_broad`. Unsound assertions are left
out of K, because an assertion that fails everywhere would otherwise "catch" every mutant. Each plan is scored twice: the model's own assertions
(**model view**: the planner alone) and what codebot froze after repairs, salvage and the guard (**frozen**: the system).

Calibration [V] (level 1): the catalog's hand-written reference plan scores E 1.0, S 1.0, K 0.75. The per-item planner with Ornith 1.0, thinking
off (3 plans, 2026-10-16) scored E 0.63 / 0.50 / 0.88, S 0.96 / 0.90 / 0.99, K 0.83 / 0.82 / 0.83: its coverage is better than the reference
plan's, its problem is impossible assertions (8 of 24 tasks).

**Worker** (reference plan, task as the unit of measure): tasks solved within the 90-minute budget, hidden-test pass rate, minutes to verified,
attempts, tool calls, generated tokens, regressions caught, thermal pause time, and the share of attempts that repeat an identical failure.

**End to end**: correct requirements (all hidden tests of the requirement pass) per 10 hours of machine time; falsely verified requirements
(codebot says done, the hidden tests disagree) reported separately and treated as worse than unfinished work; blocked tasks; thermal load.

## Candidates (pulled 2026-10-16)

| model (Ollama name) | size | intended role | notes |
|---|---|---|---|
| `ornith:9b` (as `codebot-model`) | 5.6 GB | control | [V] architecture qwen35, 9.0B, Q4_K_M. [R] DeepReinforce Ornith 1.0: Qwen3.5-9B post-trained with RL for agentic coding |
| `hf.co/bartowski/Ornith-1.5-9B-GGUF:Q4_K_M` | 6.8 GB | planner, worker | [R] newer Ornith, same lineage; community quant (no official GGUF) |
| `granite4.2:8b-q4_K_M` | 5.3 GB | planner | [R] IBM, Aug 2026, thinking switch; a different model family |
| `granite4.2:3b-q4_K_M` | 2.2 GB | worker | small enough to sit almost entirely on the GPU [H] |
| `qwen3.5:4b` | 3.3 GB | worker | same family as the control: isolates the size effect |
| `qwen3:4b` | 2.5 GB | worker | older generation, smallest memory |
| `hf.co/LiquidAI/LFM2.5-8B-A1B-GGUF:Q4_K_M` | 5.2 GB | worker | [R] sparse, about 1B active parameters: the real speed-versus-accuracy test; no official Ollama build |
| `gemma4:e4b` | 6.6 GB | worker | another family; multimodal, large download for its class |
| `gemma4:12b` | 8.0 GB | planner | largest candidate; mostly on CPU here [H] |
| `ministral-3:8b-instruct-2512-q4_K_M` | 6.0 GB | planner | lowest priority: older general model |
| `qwen3.5:9b` | 6.6 GB | ablation | the control's base model: does Ornith's training help planning? |
| `qwen-coder-model` (Qwen2.5-Coder 7B) | 4.7 GB | baseline | [V] planned worse than ornith on an older suite (58/125 vs 24/56 unsound) |

The control is never updated in place: a new version is a new candidate.

## Settings (all default to the single-model setup)

`PLANNER_MODEL`, `PLANNER_THINK`, `SPLITTER_MODEL`, `SPLITTER_THINK`, `WORKER_THINK` (the worker's model is `CODEBOT_MODEL`), `ESCALATE_MODEL`,
`THINK_BUDGET` (extra output tokens for a thinking call). The benchmark takes the same as flags (`--planner-model`, `--planner-think`, ...),
writes them into every trial and records `roles` in each trial's result; preflight refuses a run where one is exported in the shell.

## Commands

```
codebot eval mutants --levels 1-3                              build the mutant sets (no model; about 2 min)
codebot eval gate MODEL [MODEL ...] [--think on]               load, GPU share, prompt/generation speed, valid planner/worker JSON (5-10 min per model)
codebot eval planner --models A,B --think off,on --levels 1-3 --plans 5    scored planner probe; results in bench/results/eval/<run>/
codebot bench run --level 1 --mode reference-plan --model W    worker quality with fixed, validated checks
```

## Test sequence

1. **Gate** every candidate (one at a time, heat). Drop models that fail valid JSON under codebot's schemas or need a runtime change.
2. **Planner probe** (cheapest, most decisive): control thinking off vs on first, then the gate survivors proposed as planners; at least 5 plans per
   configuration on levels 1-3. Decide in advance: a planner wins only if it at least halves the share of doomed tasks (1 - E) without lowering K.
3. **Worker** with reference plans, task-level repeats, for the gate survivors proposed as workers.
4. **Integrated**: best planner + control worker, control planner + best worker, both, and the control with escalation to the best planner model.

## Results

**Gate (2026-10-16, thinking off, `bench/results/eval/gate-20261016-123545.json`)** [V]: every candidate loads on Ollama 0.40.3 and produces valid
planner and worker JSON, except LFM2.5-8B-A1B (one planner reply ran past 2,500 tokens and was cut off; its worker replies were 750-1,500 tokens
where others need 50-200). Generation speed (tokens/s): control 9.0, Ornith 1.5 7.4, Granite 8B 6.3, Granite 3B 20.4, Qwen3.5 4B 16.2, Qwen3 4B 10.8,
LFM2.5 61.0, Gemma E4B 18.2, Gemma 12B 7.3, Ministral 8B 6.4, Qwen3.5 9B 7.6, Qwen2.5-Coder 7B 10.0. No 8-12B candidate is faster than the
control; Qwen3 4B takes 7.6 GB at a 32k context (only a third on the GPU). The Gemma GPU shares in that file are wrong (a bug in the gate, fixed).

**Thinking on the planner (C0 vs C1, `bench/results/eval/20261016-124511-c0-vs-c1/`)** [V]: Ornith 1.0, level 1, 5 plans each.

| | E | S | K | Q | minutes per plan | generated tokens |
|---|---|---|---|---|---|---|
| thinking off | 0.75 ± 0.09 | 0.97 ± 0.01 | 0.82 ± 0.01 | **0.62 ± 0.07** | 7.0 | 3,411 |
| thinking on | 0.78 ± 0.16 | 0.92 ± 0.05 | 0.72 ± 0.07 | 0.56 ± 0.15 | 12.1 | 5,907 |

Thinking does not pay: the same share of doomed tasks (10 of 40 off, 9 of 40 on), lower soundness and coverage, 1.7x the time and tokens, and more
variance. It changes the kind of mistake: off, most impossible assertions are wrong expected values; on, most (17) expect a value for inputs the
spec says must raise. The planner stays thinking-off. The model view and the frozen view differ little (Q 0.60 vs 0.62): with per-item planning
the model rarely makes the mechanical mistakes codebot's repairs fix, so the remaining 25% of doomed tasks is a model-level problem. Next: other
planner models (Ornith 1.5, Granite 4.2 8B).

**Other planner models (`bench/results/eval/20261016-144707-planners-a/`)** [V]: level 1, 5 plans each, thinking off, interleaved.

| planner | E | S | K | Q | doomed tasks | minutes per plan |
|---|---|---|---|---|---|---|
| Ornith 1.0 (control) | 0.75 ± 0.09 | 0.95 | 0.81 | **0.61 ± 0.07** | 10 of 40 | 6.8 |
| Ornith 1.5 9B | 0.55 ± 0.11 | 0.89 | 0.71 | 0.39 ± 0.08 | 18 of 40 | 6.2 |
| Granite 4.2 8B | 0.47 ± 0.10 | 0.82 | 0.52 | 0.24 ± 0.04 | 21 of 40 | 12.1 |

The control is the best planner of the three on every metric. Both candidates mostly expect a value for inputs the spec says must raise (Ornith 1.5:
28 assertions, Granite: 34); Granite also writes wrong exit statuses (15), sometimes tasks with no usable assertion at all (5 repair rounds), and is
twice as slow. codebot's repairs and salvage matter more the weaker the model is: Granite's own assertions give E 0.20, after the harness 0.47; the
control's 0.72 becomes 0.75. Whether Q predicts working software is being tested with the Go calculator builds (`calc_go`).

**Go calculator builds (`calc_go`, 2026-10-16/17)** [V]: one full build per setup (per-item planner, 90-minute budget per task), then every program
tested in full: the 32 hidden tests, 20 inputs of my own that the hidden tests do not use, and a reading of the code.

| setup | builds? | hidden tests | own inputs | what the program is |
|---|---|---|---|---|
| control (Ornith 1.0) | yes | 16 / 32 | 10 / 20 | one number or `a op b` only; no precedence, no stdin mode; every error exits 1 (spec: 2 for malformed input). The run crashed mid-way (a harness bug, fixed) |
| control, rerun | yes | 13 / 32 | 9 / 20 | the same shape; its precedence task is "verified" yet 0 of 4 precedence tests pass; every error path prints `0` and exits 0; a stray `Calculator.py` |
| control, planner thinking on | yes | 6 / 32 | 5 / 20 | prints a hard-coded `42` with no input, rounds every result to a whole number, writes errors to stdout |
| Granite 4.2 8B, whole model | yes | 6 / 32 | 4 / 20 | `+` and `-` only, other operators ignored, never reports an error, crashes with no arguments |
| Ornith 1.5, whole model | yes | 3 / 32 | 1 / 20 | picks the operator by looking for a `+` inside the number |
| Ornith 1.5 as planner | no | 1 / 32 | 0 / 20 | two files in different packages; `main.go` prints three test values |
| Granite 4.2 8B as planner | no | 1 / 32 | 0 / 20 | a tokenizer demo hard-wired to `"2+3"`, plus a Python copy |

None is a working calculator. The ranking follows the planner probe's Q (control first; Ornith 1.5 and Granite as planners produced programs that
do not build), with one inversion (Granite whole 6 vs Ornith 1.5 whole 3) that one build per setup cannot settle. The more important finding was
in the harness: only 3 of the 41 checks the control rerun froze hold on the reference solution (T3 asserted `calc ""` exits 0; T4 invented `-e calc`
flags; T5 expected five different outputs from the same command; T6 expected error text on stdout and exit 2 through `go run`, which reports 1;
T7 passed `echo` as an argument instead of stdin), the worker never saw the spec, and the ladder split the impossible checks into ever deeper copies.
The scores above mostly measure that, not the models. The fixes are in `docs/dev/HANDOFF.md` ("Design review fixes"); the models should be compared
again on the fixed harness.

**Control on the fixed harness (`calc3-control`, 2026-10-17/18, fb457e7)** [V]: 20/32 hidden tests and 11/20 own inputs for the program as left,
16/32 counting only verified work (a blocked task's last attempt is not reset, and T7's unverified stdin code earns 4 of the 20), in 3 h 58 min
(the old rerun: 13/32 in 6 h 46 min). 77% of the frozen checks hold on the reference (old builds: 27-51%); nothing was marked done on a check that
fails on the reference. The re-derivation removed T1's impossible `go build -o calc . -v` check in its first real use (T1 done in 6 minutes) and
kept T3's correct precedence checks, but judged T7's twelve impossible checks (`go run . "2 + 3"`: the whole expression in one argument)
required by the spec. Precedence (T3) and stdin mode (T7) ended blocked. The program still handles one number or `a op b` only, exits 1 on every
error and prints with `fmt.Println` although the spec names `strconv.FormatFloat(v, 'f', -1, 64)`: the checks hold but are thin (the planner's
exit-2 checks went through `go run` and were dropped instead of rewritten, and the spec's prose rules have no examples), and the worker does the
minimum that passes them.

**Control after fixes 6-9 (`calc4-control`, 2026-10-18, e9ee6ea)** [V]: 15/32 hidden tests and 10/20 own inputs, as left and verified-only
alike (the code went back to the last verified checkpoint twice, with `T3.attempt5.patch` and `T6.attempt8.patch` saved), in 2 h 46 min; calc3's
verified code scores 16/32 and 10/20. By requirement, calc3 verified -> calc4: formatting 2/4 -> 4/4 (T5 used `strconv.FormatFloat`, which its
title named, so `99999 * 99999` no longer prints `9.999800001e+09`), errors 4/7 -> 1/7 (T6 blocked, and T1 wrote every message as `Error:` with
status 1 for a bad token), the rest unchanged (R1 4/4, R2 3/3, precedence 0/4, R4 3/4, stdin 0/6). 43 of the 48 frozen checks hold on the
reference (90%; calc3 77%), but the 5 that do not are 5 of T6's 6, so T6 could never pass: four expect `"stdout": ""` and
`"stdout_contains": ["error:"]` at once, one expects `calc 2 3` to print 5, and the re-derivation judged all five required by the spec. The
`go run` rewrite (fix 6) turned 15-39 planner assertions per round into `./calc` ones. The argument rule (fix 7) never fired: the planner wrote
its stdin checks as `./calc echo "2 + 3"`, because its guide never mentions the `stdin` field, and T7 was dropped from the plan after three
rounds. Precedence: attempt 3 passed all 9 of T3's checks and broke only `6 x 7` (T2), but rung 2 resets the code before every attempt, so
attempt 4 started from scratch; attempts 4 and 5 wrote parsers that never stop (`break` inside `switch`), and each check run then waited
8 x 60 s. The planner's repair replies named task 1 instead of 7, twice, and were applied to T1 (the second time the guide's `shapes.area`
example); T1 was saved by the spec's own examples. The fixes worked as built; the run failed on other defects (`docs/dev/HANDOFF.md`, "Found in
the calc4 build").

**Control after fixes 10-15 (`calc5-control`, 2026-10-18, 37614e6)** [V]: 13/32 hidden tests and 9/20 own inputs, as left and verified-only
alike (the code is T6's checkpoint; `T3.attempt4.patch`, `T7.attempt5.patch` and `T7.near.patch` saved), in 2 h 47 min. By requirement,
calc4 -> calc5: formatting 4/4 -> 2/4 (T5's 8 checks held before any work with `fmt.Println`, so `99999 * 99999` prints `9.999800001e+09`
again), the rest unchanged (R1 4/4, R2 3/3, precedence 0/4, R4 3/4, errors 1/7, stdin 0/6). 52 of the 60 checks in force hold on the reference
(87%); the 8 that do not are T7's exact error text and seven T1 checks that run `./calc cwd` with an expression on standard input, an argument
the spec never mentions, so the worker built a `cwd` mode to pass them. What each fix did: T7 got six stdin checks, five sound (the first plan
with any), and the program-name rule dropped one more; the re-check ran once and removed T1's `go build -o /dev/null . cwd`; the repair went to
the rejected task, T6, but was twelve copies of the guide's new error example (`go run . ""`, exit 2), which became T6's whole check. T6 was
rejected because the planner wrote each error input as a pair (`"stdout": "", "exit": 2` and `"stdout_contains": ["error:"]` with the default
exit 0): the plan-wide clash rule drops both of a tied pair, which removed all ten, five sound exit-status checks among them, and what was left
(`go run . 10 / 0` exits 1) holds on the seed, which does not compile. The near-success restart (fix 11) hurt: T7's first attempt rewrote
`main.go` as a stdin-only program, attempt 2 passed T7's checks and broke T1, T2, T4, T5 and T6, and attempts 3-5 started from it, saw T7's
check pass and finished without a change (attempt 4 in one action), so T7 ended blocked. No task was split. The worker spent 79 of T3's 87
model minutes writing the whole of `main.go`: 15 writes, 8 of them repeating the size of the write before (37 min), and 3 replies cut off at
the token limit (12 min); attempt 3 was five identical writes and nothing else, and the worker's own `./calc 2 + 3 * 4` ran through a shell
that expanded `*` to the file names. Same-size rewrites took 46% of the model's time in calc3 and 27% in calc5. Every hidden error test checks
that standard error starts with `error:`, and the code writes `Error:`, which no check can see. One build is one plan: the plans of calc3,
calc4 and calc5 differ in every task, so the total moves with the plan; the per-fix effects above are what the logs show.

**A plain loop without codebot (2026-10-18)** [V]: three rounds of fixes moved the score 16 -> 15 -> 13, so the baseline was measured that
had never been run. The model gets `SPEC.md` and `go.mod` in one prompt and replies with the whole `main.go`; the loop builds it and runs the 15
spec examples codebot's grounding derives (compared as codebot's runner does: exit 0, stdout stripped); the next prompt is the spec, the current
code and what failed. No planner, no tools, no model-written checks, no hidden tests. Same model and settings as the worker (`codebot-model`,
thinking off, temperature 0.2, 32k context), 6000 output tokens, at most 12 rounds. Scored with the bench's own runner (`capbench.reference`,
which gives calc5's code 13/32) and the 20 own inputs. Nine runs: the first version 25, 10, 25 of 32 (17, 2, 15 of 20); with a new sampling
seed every round, the last code block of a reply and the best version kept, 22, 7, 10 (13, 3, 3); the same with the 71 checks of the calc_go
reference plan (25ab269) as feedback instead of the examples, 26, 7, 10 (15, 3, 3). A single run is a coin flip: 4 of 9 reach 22-26 and 5
stall at 7-10 (median 10, mean 15.8; codebot 16, 15, 13), in 13-35 min against codebot's 2.7-3.9 h. Runs stall because at temperature 0.2
the model sends back the identical reply once the prompt stops changing, and a new seed did not change that: seed 2 rejected every expression
with "two numbers in a row" for four rounds. The loop's own signal separates the outcomes (12-13 of 15 examples passing went with 22-26 hidden,
0-1 with 7-10), so the best of each three runs by that signal scored 25, 22 and 26 in 54-83 min of model time. The 71 checks added 4 where the
loop moved (seed 1: 26 against 22, with 57 of the 64 non-build checks passing) and nothing where it stalled (with the same seed both arms get the
same first reply, and seeds 2 and 3 wrote the same code from round 2 under either feedback). Precedence stays the weak spot (0/4 or 2/4 in every
good run); the other misses of a good run are stdin errors that exit instead of going on to the next line, and exit statuses. The model's first
reply alone scored 1, 6 and 18 of 32 (the first version took the first of five code blocks in seed 3's reply and scored it 1). Codebot was
never run with the reference plan (`--mode reference-plan`), so whether its pipeline does better with sound checks is not measured.

**Restarting a stalled loop (2026-10-19)** [V]: the same loop with the examples as feedback, but when a draft has not improved for two rounds
(by the loop's own signal: it builds, then spec examples passed) the next round starts a fresh draft from the spec alone, and the version
delivered is the best of all drafts by that signal; 45 min per run. Same seeds as above, so each run's first draft is the same reply. Result
22, 6 and 22 of 32 (13, 2, 13 of 20) against 22, 7 and 10 without restarts: restarting rescued seed 3 (its third draft passed 13 of 15
examples 18 min in; 8 drafts in 45 min) but not seed 2 (4 drafts in 46 min, none passing more than 1 example). Seed 1 had its 13/15 version
at round 3 (3 min) and never beat it. On 2 of 3 seeds one restart run matched the best of three plain runs (22-26 in 54-83 min) in 45-54 min.
Two limits showed. A ceiling: across all 12 loop runs (59 distinct programs) no version passed more than 13 of the 15 examples, every 13/15
version failed the same two precedence examples (`./calc 2 + 3 * 4` printed 20) and the model sent the same program back when shown them;
seed 1's and seed 3's 13/15 programs are different code with the same 22/32, test for test. Runaway replies: 5 of the 51 replies ran to the
6000-token cap (12.5-13 min each, 64 of the 145 min), the model reasoning in code comments ("Actually, let's just evaluate directly." 18
times) or writing code block after block; they took 38 of seed 1's 54 min and 25 of seed 2's 46, while seed 3 had none. The two 13/15
replies were 605 and 830 tokens. Temperatures: median 89 °C, max 94 °C, one thermal pause (40 s). The loops, their drivers and the
per-run results are in `bench/baselines/`.

**The restart loop on two Python levels (2026-10-19)** [V]: to see whether calc_go generalises, the restart loop was ported to Python
(`bare_py.py`): the model writes every module the spec names, each as `### name.py` plus one code block; the feedback is the compiler's error
or the spec's own examples run by codebot's assert runner; 45 min per run (a round that starts before the cap finishes), seeds 1 and 2,
scored with the level's hidden tests. **level_01** (4 modules, 33 examples, 64 hidden tests): 63 and 60 of 64, against codebot's 50, 30 and
47 in run `20261015-180429-l1-full` (same model build, 90 min per task, 1.8 h per trial, before the regression gate, so today's pipeline is
not measured), in 41 and 46 min. The model's first reply alone scored 60 and 55: one prompt beat codebot's best trial. Seed 1 passed all 33
examples in its seventh draft; seed 2 never passed more than 31 and delivered a version that fails the same two examples seed 1's first draft
failed (`safe_filename('naïve.txt')`, `split_ext('a.')`); those two are 2 of its 4 hidden misses. **level_03** (`depgraph.py`, 8
requirements, 87 hidden tests, no codebot run to compare): both runs passed all 6 examples (8 rounds in 52 min, 4 in 31 min) and scored 58
and 63 of 87; the first replies scored 6 and 44. The examples cover only `resolve`, `find_cycle` and `groups`, nothing told the loop about
the other five requirements, and seed 1's module has no `resolve_subset` at all (R8 0 of 12). The loop is only as good as the examples it
runs. As on calc_go the repairs mostly stalled (19 of the 38 repair rounds sent the program back unchanged) and the restarts did the work:
all 4 delivered versions came from a later draft (drafts 7, 3, 3 and 2). One of the 57 replies ran to the 6000-token cap (12.8 min);
level_01 replies were 700-1,100 tokens (1.5-2 min), level_03 replies 2,000-2,900 (4-7 min). Temperatures: median 92 °C, max 94 °C, a 20 s
thermal pause before 45 of the 57 rounds (15 min in all, inside the time cap). The first attempt read `### fileutil.py` as `util.py` and was
stopped after one round; the runs above use the fixed reader. Results in `bench/baselines/results/levels/`.

**Drafts mode through the benchmark (2026-10-19)** [V]: the loop above, ported into codebot as drafts mode (`lib/codebot/drafts.py`, commit
3481000), run the way a user's project runs: `codebot bench run --level 1 --mode drafts --task-minutes 45 --repeats 2 --seed 1` (run
`20261019-200101-drafts-l1`: `codebot start`, the bash supervisor, one round per engine step, the build and the examples in the Docker sandbox, the
hidden tests after). Result **63/64 and 60/64**, the same as the baseline loop, with the same failing hidden tests. Seed 1 repeated all 21
rounds of the baseline run (the same reply lengths, restarts and example counts each round, all 33 examples at round 21, 42.2 min); seed 2
repeated the first 23 of its 24 rounds and stopped one round sooner at the 45-min cap, because drafts mode counts the sandbox runs and git
commits in a round's time (45.2 min after 23 rounds against 43.2); the baseline's 24th round changed nothing, both delivered round 11. The model
is deterministic for a given prompt and seed on this machine, so this shows the port sends the same prompts and the same feedback, and that
the sandbox, the supervisor and the state kept between steps change nothing the model sees. Thermal pauses were the same (340 s, 420 s). It is
the first time the benchmark reports a level as held (median 96%, the bar is 80%). The one miss of seed 1 is requirement 7 (`is_valid_ipv4`
rejecting non-ASCII digits), which has no example in the spec. The Go path of drafts mode (calc_go) has only been tested with the fake model.

**Tuning drafts mode from the recorded rounds (2026-10-19)** [V for the analysis; the effect is not yet measured]: all 13 recorded loop runs
(calc_go batches 2-3, level_01, level_03) have every round's code, so three questions could be answered without the model. (1) Which rounds
gave a new best version (the one a run delivers)? 8 were fresh drafts, 14 a draft's first repair, 3 a second repair and 1 a third, and every
one of those later repairs came right after a repair that had improved. The 54 repairs that followed a repair that did not improve (34 of them
after the model sent the program back unchanged) improved their draft once and never gave a new best. So a draft now ends at its first repair
that does not improve (`DRAFTS_PATIENCE=1`, was 2), which frees about half of all repair rounds for fresh drafts. (2) 10 replies ran to the
6000-token cap (12.5-15 min each) and none was useful, while no complete reply that built was longer than 2.93 times the longest complete
reply that had built before it in the same run. A reply's cap is now 3 times that length, at least 3000 tokens (6000 until something has built):
it would have cut none of the useful replies and shortened 3 of the runaways by 6-7.5 min each and 2 by about 1 min. (3) In Python, a version must define every
function the spec declares and, for a one-module spec, every exception it names (read from the syntax tree, so no model code runs; builtins such
as `str` in `str(error)` do not count). Only one recorded version lacked one, and it was the one level_03 seed 1 delivered: all 6 examples
passed and the run stopped without `resolve_subset` (requirement 8: 0 of 12 hidden tests). These change what the model sees, so drafts mode no
longer replays the baseline runs; the prompts are otherwise the same.

**The tuned drafts mode, measured (2026-10-20)** [V]: commit 4c5b056, 45 min per run (runs `20261019-221143-drafts2-calc`,
`20261020-003318-drafts2-l3`, `20261020-015546-drafts2-l1`). Hidden tests: level_01 **63/64 and 61/64** (patience 2 gave 63 and 60; 10 and 11
drafts in 21 and 23 rounds against 7 and 7), held with a median of 97%. level_03 **74/87 and 44/87** (the baseline loop: 58 and 63). Seed 1
passed all 6 examples and all 9 declared names after 32 min and stopped, with `resolve_subset` there this time (requirement 8: 11 of 12). Seed
2 spent 29 of its 47 min on two replies that ran to the 6000-token cap (the reply cap does not bite at this program size: 3 x 2925 tokens is
more than 6000) and delivered a `resolve_subset` that fails all 12 of its hidden tests. calc_go **6, 21 and 1 of 32** (the Go restart loop: 22, 6
and 22; the task pipeline: 16, 15 and 13). 8 of its 11 fresh drafts ran to the cap (13 min each, 99 of 136 model minutes); seed 3 never built
in 47 min and seed 1 first built at minute 46. Two causes, both in the port, not in the three rules: (1) the calc spec names no file, so the first
prompt asked for "every file", and the model wrote README.md and go.mod first in all 11 fresh drafts, then rewrote main.go and argued with itself
in prose or code comments until it was cut off (the Go loop, which asked for main.go only: no README, 6 of 24 fresh drafts cut off). (2) A
reply cut off inside a rewrite was judged on the cut-off copy, never on the complete version written earlier in the same reply. Judged on that
earlier block instead (the same judging, in the sandbox), 4 of the 10 cut-off replies of these runs build, where none did; one is calc seed 1's
round 5 with 11 of 15 examples, in a run that ended at 3. Changed since (not yet measured): the first prompt names the files to write and asks
for no other (`main.go and any other .go file the program needs` when the spec names none), and a cut-off block no longer replaces a complete
block of the same file.

**Stopping replies that repeat themselves (2026-10-20)** [V for the analysis; not yet measured end to end]: with the prompt fixed, calc_go seed
1's first reply began with main.go instead of README.md, but it still ran to the cap, 13 min: the same 12 comment lines about operator
precedence, 28 times over. All 22 replies that ran to the cap in the recorded runs (the loop baselines and drafts mode) were loops like that.
drafts mode now streams the reply and stops it once its last 8 non-blank lines have appeared twice before (`drafts.loop_watch`). On the
recorded replies that stops 21 of the 22, at a median of 32% of their length (about 165 of the 290 model minutes they took), and 2 of the 289
replies that ended on their own; both were writing their file for the third time, and with the cut-off rule above such a reply is judged on
the copy before. On the real server, closing the stream stops the generation: after a reply stopped at 6 tokens the next request answered in
1.4 s (the full 400-token reply takes 44 s). The next repair is told "Your last reply was stopped because it repeated the same lines over and over."

## Known limits

- Level 1 is pure functions with literal examples, which flatters planners that copy examples; levels 2-3 have fewer focused mutants (79 and 27).
- Sampling differs between models even with a fixed seed; compare means over several plans, not single runs.
- No splitter score yet: splitter replies are now saved (`split.reply.<task>.json`) so a splitter corpus can be built from real runs.
- Task-level worker repeats are not automated yet; `--mode reference-plan` runs whole trials.
