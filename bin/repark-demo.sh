#!/usr/bin/env bash
# Return the demo estate to its parked state WITHOUT touching source.
#
# Why this exists: the obvious one-liner is
#     make clear-demo && git checkout -- catalog reports specs ...
# and it is WRONG, twice over — `catalog/` also holds `catalog/bootstrap/*.py`
# and `specs/` also holds `specs/platform/constitution.yaml`. Reverting whole
# directories silently discarded uncommitted SOURCE changes twice in one
# session: a constitution clause, and the correlator's confidence fix. Both
# times the loss was invisible until something failed much later.
#
# So: revert only TRACKED DATA paths, never a directory that mixes data with
# code, and refuse loudly if a source file would have been caught.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"

# Tracked fixture data that a demo run legitimately rewrites.
DATA_PATHS=(
  AGENTS.md
  .agents
  registry/repo-registry.yaml
  reports/plans
  reports/runs/reviews.json
  testplans
  specs/PROJ-301
)
# Catalog DATA only — never catalog/bootstrap/ (code) or catalog/templates/.
# `catalog/review/*` is deliberately NOT used: that directory also holds
# export_review_queue.py. The guard below caught exactly that on the first run,
# which is the whole reason this file is a script and not a one-liner.
mapfile -t CATALOG_DATA < <(git ls-files 'catalog/*.jsonl' 'catalog/review/*.csv' 2>/dev/null || true)

# Scratch a run leaves behind that is not tracked at all.
CLEAN_PATHS=(reports/runs reports/openhands reports/exports testdata out/gates)

python3 engine/lib/demo_data.py >/dev/null 2>&1 || true

restore=("${DATA_PATHS[@]}")
[ "${#CATALOG_DATA[@]}" -gt 0 ] && restore+=("${CATALOG_DATA[@]}")

# Guard: nothing in the restore set may be source. If this ever trips, the list
# above grew a code path and the fix is to narrow it — not to delete this check.
for p in "${restore[@]}"; do
  case "$p" in
    *.py|*.sh|*bootstrap*|*/platform/*)
      echo "REFUSING: '$p' looks like source, not demo data" >&2; exit 1 ;;
  esac
done

git checkout -- "${restore[@]}" 2>/dev/null || true
for p in "${CLEAN_PATHS[@]}"; do
  git clean -fdq "$p" 2>/dev/null || true
done
# Second pass: clear-demo regenerates AGENTS.md and the skills after the first.
git checkout -- AGENTS.md .agents 2>/dev/null || true

dirty=$(git status --porcelain | grep -vE '^\?\?' | awk '{print $2}' \
        | grep -E '\.py$|\.sh$|bootstrap/|platform/' || true)
if [ -n "$dirty" ]; then
  echo "note: source files are modified (left untouched, as intended):"
  echo "$dirty" | sed 's/^/  /'
fi
echo "estate re-parked"
