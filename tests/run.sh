#!/usr/bin/env bash
# Run the harness tests (fast, no real model). Usage: tests/run.sh [pattern]
cd "$(dirname "$0")" && exec python3 -m unittest discover -s . -p "${1:-test_*.py}" -v
