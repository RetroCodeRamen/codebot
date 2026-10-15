"""Capability Ladder Benchmark: 12 progressively harder projects plus a growing-application benchmark, run through the REAL codebot harness
(planner -> freeze -> supervisor loop -> worker -> frozen checks -> recovery ladder), with a per-task wall-clock budget (lib/codebot/taskclock.py),
independent reference tests the agent never sees, resumable run state and reports. Data lives in bench/ladder/ (catalog, git-tracked) and
bench/results/ladder/ (run history, git-ignored with the other results)."""
VERSION = "1.0.0"          # the benchmark framework version; every catalog manifest has its own version too
