#!/usr/bin/env bash
# PR-review context bundle — ONE read-only command instead of five improvised ones.
# Bundled with the skill (AgentSkills scripts/ convention): the agent is told to run
# this, so every review starts from the same deterministic evidence.
#
# Usage: gather-context.sh <app_repo> <pr_number>
set -uo pipefail
REPO=${1:?app repo}; PR=${2:?pr number}
cd "${AIQE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"

echo "== Routing (which E2E repos this PR touches) =="
bash adapters/mock/scm.sh changed_files "$REPO" "$PR" > out/.pr-review-changed.txt 2>/dev/null \
  || python3 -c "import sys;sys.exit(0)"
python3 engine/phases/resolve.py pr "$REPO" \
  --changed-files out/.pr-review-changed.txt 2>/dev/null \
  || echo "(resolver unavailable — check the repo name)"

echo
echo "== Existing coverage for $REPO (extend before create) =="
python3 bin/qa.py sql \
  "SELECT test_repo, file, title FROM tests WHERE app_repo='$REPO' LIMIT 25" 2>/dev/null \
  || echo "(catalog index empty — run make catalog-db)"

echo
echo "== Coverage gaps ([NO TEST] surface) =="
python3 bin/qa.py gaps --repo "$REPO" 2>/dev/null | head -30 || true

echo
echo "== The repo's existing approach (mirror this, do not invent one) =="
sed -n '1,60p' out/repo-conventions.md 2>/dev/null \
  || echo "(no exemplars yet — run the pipeline once so they are generated)"
