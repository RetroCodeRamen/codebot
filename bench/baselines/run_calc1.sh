#!/bin/bash
# Batch 1 (2026-10-18): three bare.py runs on calc_go, 12 rounds / 45 min each, then unload the model and score. Usage: run_calc1.sh OUT_DIR
B=$(dirname "$(realpath "$0")"); OUT=$(realpath -m "${1:?usage: run_calc1.sh OUT_DIR}"); mkdir -p "$OUT"; . "$B/_common.sh"; env_start "$OUT"
for s in 1 2 3; do
  echo "== seed $s $(date +%T)"; python3 "$B/bare.py" "$OUT/seed$s" --seed $s --rounds 12 --minutes 45 > "$OUT/seed$s.log" 2>&1; echo "exit=$? $(date +%T)"; tail -1 "$OUT/seed$s.log"
done
env_stop
python3 "$B/score.py" "$OUT"/seed{1,2,3} > "$OUT/score.txt" 2>&1; echo "scored $(date +%T)"; cat "$OUT/score.txt"
