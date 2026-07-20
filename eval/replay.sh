#!/usr/bin/env bash
# Replay the benchmark set (10 historical PRs + 10 closed tickets) against the
# current prompts/policy. Fixtures live in eval/benchmark/{prs,tickets}/*.json.
set -euo pipefail
mkdir -p eval/results
for f in eval/benchmark/prs/*.json eval/benchmark/tickets/*.json; do
  [ -e "$f" ] || continue
  echo "replaying $f"
  # Fixture-first: each fixture pins trigger inputs + expected resolution/artifacts.
  python3 eval/run_fixture.py "$f" > "eval/results/$(basename "$f")" || echo "FAIL $f"
done
