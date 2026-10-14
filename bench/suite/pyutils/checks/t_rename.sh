#!/usr/bin/env bash
if grep -rn old_name --include='*.py' . ; then echo "old_name is still used (see lines above); rename it to double everywhere"; exit 1; fi
out=$(python3 main_app.py 2>&1); [[ "$out" == "42" ]] || { echo "python3 main_app.py printed: $out (expected 42)"; exit 1; }
