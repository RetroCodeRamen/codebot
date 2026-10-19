#!/bin/bash
# 2026-10-19: the restart loop (bare_py.py) on level_01 and level_03, two runs of 45 min each, then unload the model and score with the
# levels' hidden tests. Usage: run_levels.sh OUT_DIR
B=$(dirname "$(realpath "$0")"); OUT=$(realpath -m "${1:?usage: run_levels.sh OUT_DIR}"); mkdir -p "$OUT"; . "$B/_common.sh"; env_start "$OUT"
for lv in level_01 level_03; do for s in 1 2; do
  echo "== $lv seed $s $(date +%T)"; python3 "$B/bare_py.py" $lv "$OUT/$lv-seed$s" --seed $s --minutes 45 > "$OUT/$lv-seed$s.log" 2>&1; echo "exit=$? $(date +%T)"; tail -1 "$OUT/$lv-seed$s.log"
done; done
env_stop
for lv in level_01 level_03; do python3 "$B/score_py.py" $lv "$OUT/$lv"-seed{1,2}; done > "$OUT/score.txt" 2>&1; echo "scored $(date +%T)"; cat "$OUT/score.txt"
