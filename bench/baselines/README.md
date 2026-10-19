# Baselines: the model without codebot

Plain loops that give the model the spec and take whole files back, judged only by the spec's own examples. They measure what codebot's
pipeline has to beat. The results and what they mean are in `docs/MODEL_EVAL.md` ("A plain loop without codebot" and the paragraphs after it).

| File | What it is |
|---|---|
| `bare.py` | calc_go loop, first version (batch 1, 2026-10-18) |
| `bare2.py` | the last code block, a new seed every round, continue from the best version; `--feedback plan` (batch 2) |
| `bare3.py` | restarts: a fresh draft after `--patience` rounds without improvement (batch 3, 2026-10-19) |
| `bare_py.py` | the restart loop for the Python levels: several modules, the examples run by codebot's assert runner |
| `score.py`, `handtest.py` | calc_go scoring: the 32 hidden tests (the bench's runner) and the 20 hand inputs |
| `score_py.py` | Python-level scoring: the level's hidden tests |
| `make_calc_plan.py` | writes `bench/ladder/catalog/calc_go/reference_plan.json` |
| `run_calc1.sh` ... `run_calc3.sh`, `run_levels.sh` | the drivers as run: one model job after another, temperatures every 60 s, unload, score |
| `results/` | `calc_go/batch1` ... `batch3`, `levels/` (level_01 and level_03); per run: `summary.json`, `rounds.jsonl`, `score.json` and the delivered code; the model's replies are not kept |

Every loop talks to the private Ollama on 127.0.0.1:11500 (`codebot-model`); `--fake REPLY_FILE ...` replays files instead. The drivers take an
output directory: put it outside the repo (a calc_go run keeps Go caches of about 100 MB).
