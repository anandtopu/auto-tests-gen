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
#
# The mirror-image mistake is DELETING parked state, and this script made it
# too: `reports/runs` mixes scratch with tracked fixture records, clear-demo
# removes run records by pattern, and `git clean` only touches UNTRACKED files
# — so two parked records stayed deleted. Hence the restore pass and the
# tracked-deletions backstop at the bottom. Both directions of the same rule:
# a path that mixes categories needs a check, not a wildcard.
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

# Restore pass. The CLEAN_PATHS also MIX data with data: `reports/runs` holds
# both scratch from this session's runs and TRACKED fixture records that are
# part of the parked estate. clear-demo deletes run records by pattern and
# cannot tell the two apart, and `git clean` only removes UNTRACKED files — so
# nothing here ever put the fixtures back. Two of them stayed deleted until a
# human happened to read `git status` before a commit; a commit landing first
# would have removed parked fixtures from the repo as a side effect of tidying
# up. Anything TRACKED that is now missing under these paths was parked state.
GONE=()   # mapfile may leave the name unset when it reads no lines; set -u.
mapfile -t GONE < <(git ls-files --deleted -- "${CLEAN_PATHS[@]}" 2>/dev/null || true)
if [ "${#GONE[@]}" -gt 0 ]; then
  # Refuse the WHOLE list, as the sibling guard does: if a code path turned up
  # here, CLEAN_PATHS is wrong and acting on any of it is acting on a list we
  # have just declared untrustworthy. Say that nothing was restored, though —
  # exiting here skips the backstop below, so this message is the only warning
  # the estate is still missing files.
  for p in "${GONE[@]}"; do
    case "$p" in
      *.py|*.sh|*bootstrap*|*/platform/*)
        echo "REFUSING: '$p' looks like source, not demo data" >&2
        echo "  Nothing was restored. CLEAN_PATHS covers a code directory;" >&2
        echo "  narrow it. These tracked files are still deleted:" >&2
        printf '    %s\n' "${GONE[@]}" >&2
        exit 1 ;;
    esac
  done
  git checkout -- "${GONE[@]}" 2>/dev/null || true
fi

# Backstop: re-parking means the tracked tree is back to HEAD. A tracked file
# still missing anywhere means this script deleted parked state and did not
# restore it — say so LOUDLY and fail, because the next step is a commit and
# the loss would ride along in it unnoticed.
left=$(git ls-files --deleted || true)
if [ -n "$left" ]; then
  echo "ERROR: tracked files are still deleted after re-parking:" >&2
  echo "$left" | sed 's/^/  /' >&2
  echo "The estate is NOT parked. Restore them before committing." >&2
  exit 1
fi

dirty=$(git status --porcelain | grep -vE '^\?\?' | awk '{print $2}' \
        | grep -E '\.py$|\.sh$|bootstrap/|platform/' || true)
if [ -n "$dirty" ]; then
  echo "note: source files are modified (left untouched, as intended):"
  echo "$dirty" | sed 's/^/  /'
fi
echo "estate re-parked"
