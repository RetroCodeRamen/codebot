#!/usr/bin/env bash
out=$(python3 cli.py slug "Hello World" 2>&1); [[ "$out" == "hello-world" ]] || { echo "cli.py slug printed: $out (expected hello-world)"; exit 1; }
out=$(python3 cli.py kv "a=1;b=2" 2>&1); python3 -c 'import json,sys; assert json.loads(sys.argv[1]) == {"a": 1, "b": 2}' "$out" 2>/dev/null || { echo "cli.py kv printed: $out (expected JSON {\"a\": 1, \"b\": 2})"; exit 1; }
