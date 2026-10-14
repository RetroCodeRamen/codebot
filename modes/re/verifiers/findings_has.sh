#!/usr/bin/env bash
# usage: findings_has.sh [--sourced] <findings file> <required text>...
# Exit 0 only if the file exists, contains every required text, and (with --sourced) every bullet ends with [src: ...]. Says what is missing.
sourced=0; [[ "${1:-}" == --sourced ]] && { sourced=1; shift; }
f="${1:?findings file}"; shift
[[ -f "$f" ]] || { echo "findings file $f does not exist yet"; exit 1; }
rc=0
for need in "$@"; do grep -qF -- "$need" "$f" || { echo "findings file lacks: $need"; rc=1; }; done
if (( sourced )); then
  bad=$(grep -E '^\s*[-*]\s' "$f" | grep -vE '\[src: [^]]+\]' | head -3)
  [[ -z "$bad" ]] || { echo "bullets without a [src: ...] tag:"; echo "$bad"; rc=1; }
fi
exit $rc
