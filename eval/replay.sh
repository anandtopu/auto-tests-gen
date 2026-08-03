#!/usr/bin/env bash
# Replay the benchmark set (10 historical PRs + 10 closed tickets) against the
# current prompts/policy. Fixtures live in eval/benchmark/{prs,tickets}/*.json.
set -euo pipefail
# The transaction log is REDIRECTED. A replayed fixture is not a transaction on
# this estate, but its gate emissions landed in the real audit log all the same
# — 299 `gate.committed` and 247 `gate.no_changes` entries an operator would
# read as work that happened here. `make maintain` also evaluates alert rules
# over that log and delivers through the Notify port.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/out/test-events"
export AIQE_EVENTS_DIR="$(cd "$ROOT/out/test-events" && pwd -W 2>/dev/null || pwd)"
mkdir -p eval/results
for f in eval/benchmark/prs/*.json eval/benchmark/tickets/*.json; do
  [ -e "$f" ] || continue
  echo "replaying $f"
  # Fixture-first: each fixture pins trigger inputs + expected resolution/artifacts.
  python3 eval/run_fixture.py "$f" > "eval/results/$(basename "$f")" || echo "FAIL $f"
done
