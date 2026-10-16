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

## Known limits

- Level 1 is pure functions with literal examples, which flatters planners that copy examples; levels 2-3 have fewer focused mutants (79 and 27).
- Sampling differs between models even with a fixed seed; compare means over several plans, not single runs.
- No splitter score yet: splitter replies are now saved (`split.reply.<task>.json`) so a splitter corpus can be built from real runs.
- Task-level worker repeats are not automated yet; `--mode reference-plan` runs whole trials.
