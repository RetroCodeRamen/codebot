#!/usr/bin/env bash
# One placement/runtime config: load, then the two real repair prompts and one short JSON request at 16K context; stop the server.
#   compare.sh LABEL RUNTIME [llama-server flags...]
set -u
B=$(dirname "$0"); label=$1 runtime=$2; shift 2
H="$(cd "$(dirname "$0")/../.." && pwd)"; M=${MODEL:-$H/models/gguf/Qwen3-Coder-30B-A3B-Instruct-UD-IQ3_XXS.gguf}
R=$H/bench/results/bigmodel/$label
sw() { awk '/^pswpin|^pswpout/{print $2}' /proc/vmstat | tr '\n' ' '; }
a=($(sw)); s0=$(awk '/SwapFree/{print $2}' /proc/meminfo)
bash $B/launch.sh "$label" "$runtime" "$M" 18080 -c 16384 --cache-ram 0 "$@" || exit 1
grep -E "model buffer size|KV buffer size|compute buffer size|layers, .*MiB used|moe.cache|MoE cache" "$R/server.log" | sed 's/^[0-9.]* I //' | cut -c1-140
python3 $B/screen.py "$label" 18080 "$(cat $R/server.pid)" --ctx 16384 --max-tokens 6000 --only json_action,calc_repair,l03_repair
grep -E "VmRSS|RssAnon|RssFile|VmSwap" /proc/$(cat $R/server.pid)/status | tr '\n' ' '; echo
kill "$(cat $R/server.pid)"; sleep 5
b=($(sw)); s1=$(awk '/SwapFree/{print $2}' /proc/meminfo)
echo "whole config: swap in $(( (b[0]-a[0])*4/1024 )) MB, out $(( (b[1]-a[1])*4/1024 )) MB, swap free $((s0/1024)) -> $((s1/1024)) MB"
