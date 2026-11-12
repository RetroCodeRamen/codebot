# Drafts mode against a designed team

`compare.py` runs the same specs with drafts mode and with a team the Team Designer proposes, with the same seeds and time cap, and scores both
with the catalog's hidden tests. Results are appended to `results/<name>.jsonl` (one line per trial); a rerun with the same name continues.

```
python3 bench/teams/compare.py --name NAME [--tasks team_a,team_b] [--arms drafts,team] [--seeds 1,2,3] [--minutes 20]
```

The tasks are `bench/ladder/catalog/team_a` (a shop pricing library: `money.py`, `catalog.py`, `cart.py`; 18 runnable examples, 70 hidden tests)
and `team_b` (a meeting planner: `clock.py`, `spans.py`, `planner.py`; 19 examples, 67 hidden tests). They were written on 2026-11-12 for this
comparison because the ladder has no multi-file task that both arms can run and that was never used for tuning; specs, reference solutions and
hidden tests were finished and validated before any model saw them (`validate_level`: the tests pass on the reference and 0 pass on the seed;
every spec example holds on the reference). They were written by the same session that wrote the pipeline's agent tools: held out from tuning,
not independent.

## heldout1 (2026-11-12, coder30b-iq3, NUM_CTX 16384, commit a0cd91d, 20 minutes per trial)

| task | arm | hidden tests, seeds 1 2 3 | model calls | minutes | how it ended |
|---|---|---|---|---|---|
| team_a | drafts | 69, 69, 69 of 70 | 3, 7, 6 | 5.6, 15.5, 13.5 | all 18 examples pass, three times |
| team_a | team | 68, 67, 66 of 70 | 49, 67, 42 | 14.9, 20.0, 20.0 | gate passed; gate failed 3 times (17 of 18); time cap |
| team_b | drafts | 54, 56, 57 of 67 | 6, 4, 5 | 21.8, 20.2, 22.0 | time cap three times, 18 of 19 examples |
| team_b | team | 39, 39, 54 of 67 | 42, 60, 49 | 20.1, 20.2, 20.1 | time cap three times (0, 15 and 17 of 19 examples at the last gate or checkpoint) |

Drafts scored higher in all six pairs: by 1 to 3 tests on team_a and by 15, 17 and 3 on team_b. The team used 42 to 67 model calls against
3 to 7, and 116,000 to 220,000 prompt tokens against 6,000 to 14,000: every agent turn reads its whole context again. One of the six team runs
ended with a passing gate. The designer proposed the same shape every time: one worker per module and an integrator.

What it does not show:
- The cap binds: four of six team runs and all three drafts runs on team_b ended at 20 minutes, so these are scores at 20 minutes, not ceilings.
- The team wastes turns that a fix would save: in every team run agents were stopped by the three-refusals rule after their check had
  passed, instead of finishing (15 such stops in the six runs).
- Both tasks are small enough for one reply (about 100 lines over three files). A team is meant for programs that are not; nothing here tests that.
- Two tasks, three seeds, one model.
