# Drafts mode against orchestrated builds

`compare.py` runs the same spec with drafts mode and with orchestrated builds, with the same model, seed and wall-clock budget, and scores each
with a catalog entry's hidden tests. The tasks are the toolbox benchmarks (`bench/toolbox/README.md`), whose expected outputs are the real tools'.

```
python3 bench/teams/compare.py --name NAME [--tasks toolbox_04,toolbox_10] [--arms drafts,plan,review] [--seeds 1,2,3] [--minutes 30]
```

| arm | what runs |
|---|---|
| `drafts` | drafts mode alone |
| `plan` | planner, then the drafts loop |
| `review` | planner, drafts loop, a fresh reviewer, fix |
| `full` | manager, one team per part, integration, QA |
| `auto` | whatever the Team Designer's size estimate chooses |

Two questions, kept apart:
- **Does orchestration help on the same workload?** One size, the arms next to each other.
- **Does it help as the work grows?** The same arms at 4, 10 and 20 tools; the designer's context-fit estimate is recorded with what the run
  actually used.

Rules of the measurement: one trial is one task, one arm, one seed, in its own project and code directory; a stage of a team run is never
counted as a trial; every arm gets the same minutes for everything it does (an orchestrated arm's design call included; the scheduler ends the
run `EXHAUSTED` when they are used); trials run one at a time. A result line records the hidden tests passed, how the run ended (`result`),
wall time, model calls and tokens, gate retries, regressions, rollbacks, agents that ran out of turns or time, human decisions, and the size
estimate against the actual size. `results/NAME.jsonl` is appended to; a rerun of the same name continues.

No series has been run with the current code yet.

## Retired: heldout1 (2026-11-12)

`results/heldout1.jsonl` is a series on two tasks that this project's assistant wrote itself, spec, reference solution and hidden tests. They
were removed the same day and are not a goal or a yardstick in any capacity: an expected outcome written by an AI is not a known outcome. The
file is kept as a record of the run, nothing more.
