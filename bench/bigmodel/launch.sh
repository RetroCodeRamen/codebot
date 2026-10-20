#!/usr/bin/env bash
# Start one llama-server for a screening run and wait until the model is loaded.
#   launch.sh LABEL RUNTIME MODEL_GGUF PORT [llama-server flags...]
# RUNTIME: ollama  = codebot's private Ollama's bundled llama-server (llama 0.5.0-dev, commit 631109b34, ggml 0.25.3, CUDA 13 backend),
#                    the runtime the ornith baseline runs on
#          b11755  = upstream llama.cpp build b11755 (prebuilt ubuntu-cuda-12.8-x64), which has --moe-cache-mib (PR #29887)
# Writes $OUT/LABEL/{server.log,server.pid,launch.json}; prints the cold-start time (exec -> /health ok).
set -euo pipefail
label=$1 runtime=$2 model=$3 port=$4; shift 4
H="$(cd "$(dirname "$0")/../.." && pwd)"
OUT=${OUT:-$H/bench/results/bigmodel}
d=$OUT/$label; mkdir -p "$d"
case "$runtime" in
  ollama) bin=$H/tools/ollama/lib/ollama/llama-server; export LD_LIBRARY_PATH=$H/tools/ollama/lib/ollama:$H/tools/ollama/lib/ollama/cuda_v13
          export GGML_BACKEND_PATH=$H/tools/ollama/lib/ollama/cuda_v13/libggml-cuda.so ;;   # Ollama starts it in cuda_v13/; elsewhere it finds no GPU
  b11755) b=$H/tools/llama.cpp/b11755; bin=$b/llama-b11755/llama-server; export LD_LIBRARY_PATH=$b/llama-b11755:$b/cudart-llama-b11755-bin-ubuntu-cuda-12.8-x64 ;;
  *) echo "unknown runtime $runtime" >&2; exit 2 ;;
esac
args=(--model "$model" --host 127.0.0.1 --port "$port" --no-webui -np 1 "$@")
python3 - "$d/launch.json" "$runtime" "$bin" "${args[@]}" <<'EOF'
import json, sys, time
json.dump({"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "runtime": sys.argv[2], "bin": sys.argv[3], "args": sys.argv[4:]}, open(sys.argv[1], "w"), indent=1)
EOF
t0=$(date +%s.%N)
nohup "$bin" "${args[@]}" > "$d/server.log" 2>&1 &
echo $! > "$d/server.pid"
for i in $(seq 1 1800); do
  if ! kill -0 "$(cat "$d/server.pid")" 2>/dev/null; then echo "server exited during load; see $d/server.log" >&2; tail -20 "$d/server.log" >&2; exit 1; fi
  if curl -sf "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    t1=$(date +%s.%N); echo "loaded in $(python3 -c "print(round($t1-$t0,1))") s (pid $(cat "$d/server.pid"))"; exit 0
  fi
  sleep 1
done
echo "not loaded after 30 min" >&2; exit 1
