# Drafts mode against a designed team

`compare.py` runs the same specs with drafts mode and with the team the Team Designer proposes, with the same seeds and time budget, and scores
both with a catalog entry's hidden tests. Results are appended to `results/<name>.jsonl` (one line per trial); a rerun with the same name
continues. The team arm is also scored at the commit its build step left (`build_hidden`).

```
python3 bench/teams/compare.py --name NAME [--tasks toolbox_04,toolbox_10] [--arms drafts,team] [--seeds 1,2,3] [--minutes 20] [--team-extra 15]
```

The tasks are the toolbox benchmarks (`bench/toolbox/README.md`), whose expected outputs are the real tools'.

## Retired: heldout1 (2026-11-12)

`results/heldout1.jsonl` is a series on two tasks (`team_a`, `team_b`) that this project's assistant wrote itself, spec, reference solution and
hidden tests. They were removed the same day and are not a goal or a yardstick in any capacity: an expected outcome written by an AI is not a
known outcome. What the series showed is only what the user's rule already says: both tasks fit in one agent's context window, and there one
agent (drafts) did as well or better at a fraction of the calls. The file is kept as a record of the run, nothing more.
