#!/bin/bash
# Batch 2 (2026-10-18): six bare2.py runs on calc_go, 12 rounds / 20 min each, alternating the feedback: the spec's examples or the 64 checks of
# the calc_go reference plan. Then unload the model and score. Usage: run_calc2.sh OUT_DIR
B=$(dirname "$(realpath "$0")"); OUT=$(realpath -m "${1:?usage: run_calc2.sh OUT_DIR}"); mkdir -p "$OUT"; . "$B/_common.sh"; env_start "$OUT"
for s in 1 2 3; do for fb in examples plan; do
  echo "== $fb seed $s $(date +%T)"; python3 "$B/bare2.py" "$OUT/$fb-seed$s" --seed $s --rounds 12 --minutes 20 --feedback $fb > "$OUT/$fb-seed$s.log" 2>&1; echo "exit=$? $(date +%T)"; tail -1 "$OUT/$fb-seed$s.log"
done; done
env_stop
python3 "$B/score.py" "$OUT"/{examples,plan}-seed{1,2,3} > "$OUT/score.txt" 2>&1; echo "scored $(date +%T)"; cat "$OUT/score.txt"
