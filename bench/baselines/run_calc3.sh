#!/bin/bash
# Batch 3 (2026-10-19): three bare3.py (restart loop) runs on calc_go, spec-example feedback, 45 min each, then unload the model and score.
# Usage: run_calc3.sh OUT_DIR
B=$(dirname "$(realpath "$0")"); OUT=$(realpath -m "${1:?usage: run_calc3.sh OUT_DIR}"); mkdir -p "$OUT"; . "$B/_common.sh"; env_start "$OUT"
for s in 1 2 3; do
  echo "== restart seed $s $(date +%T)"; python3 "$B/bare3.py" "$OUT/restart-seed$s" --seed $s --minutes 45 > "$OUT/restart-seed$s.log" 2>&1; echo "exit=$? $(date +%T)"; tail -1 "$OUT/restart-seed$s.log"
done
env_stop
python3 "$B/score.py" "$OUT"/restart-seed{1,2,3} > "$OUT/score.txt" 2>&1; echo "scored $(date +%T)"; cat "$OUT/score.txt"
