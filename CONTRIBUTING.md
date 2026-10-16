# Contributing

Thanks for looking. codebot is a research project about one question: how much real software a small local model can finish unattended when the
harness, not the model, makes the decisions. Contributions that make that measurably better, or measure it more honestly, are welcome.

## Before you start

- Read `README.md`, then `docs/dev/HANDOFF.md` (file map, current state, gotchas) and the relevant part of `docs/dev/ROADMAP.md`.
- Run `codebot doctor` and `tests/run.sh` (about 6 minutes; no model needed).

## Rules the project holds itself to

- **Local only.** No change may make codebot send data off the machine by default.
- **The model never judges itself.** Frozen checks, the baseline guard and the regression gate decide; do not add paths where model output can
  change an acceptance criterion after it is frozen.
- **Tests with every change**, and a commit only when the suite passes (check the test command's own exit status, not a piped `tail`).
- **Real-model evidence beats fake-model tests.** If a change is meant to make the agent better, say how you measured it (`docs/BENCHMARK.md`,
  `docs/MODEL_EVAL.md`), with numbers, including what got worse. A change to how assertions are repaired or validated must keep
  `tests/test_w_corpus.py` green: no rule may reject or change an assertion that holds on the reference solution.
- **Docs follow the code.** `tests/test_r_docs.py` fails when a command, config key or path is missing from the docs.
- Standard library Python only in `lib/`; match the surrounding style (dense, commented where a decision is not obvious).

## Good first contributions

- A `codebot plan-auto` command that runs the unattended planner on your own spec.
- More benchmark levels' mutant sets and splitter replies for the replay corpus (`tests/corpus/build_corpus.py`).
- Running `codebot eval gate` / `codebot eval planner` on hardware we do not have and reporting the numbers.
