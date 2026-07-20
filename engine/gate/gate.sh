#!/usr/bin/env bash
# Deterministic Quality Gate (architecture §5.5) — the ONLY place push happens.
# Runs INSIDE one writable test repo. Usage: gate.sh <KEY> <test_repo_name>
set -euo pipefail
KEY=${1:?key}; TREPO=${2:?test_repo_name}
REPORT_DIR="${AIQE_ROOT:-.}/reports"; mkdir -p "$REPORT_DIR"

CHANGED=$(git diff --name-only HEAD)
[ -z "$CHANGED" ] && { echo "NOTHING_TO_COMMIT"; exit 0; }

# 1. Scope: only test-repo content + catalog sidecars
if echo "$CHANGED" | grep -vE '^(tests/|suites/|fixtures/|data/|pages/|catalog/)' ; then
  echo "SCOPE_VIOLATION"; exit 2
fi

# 2. Born-mapped check: every new spec has a catalog sidecar entry
NEW_SPECS=$(git diff --name-only --diff-filter=A HEAD | grep -E '\.spec\.(ts|js)$' || true)
for spec in $NEW_SPECS; do
  grep -q "$spec" catalog/*.jsonl 2>/dev/null || { echo "UNMAPPED_TEST: $spec"; exit 4; }
done

# 3. Static checks (per-repo commands may be overridden in .ai-qe/config.yaml)
npm run lint --if-present && npx tsc --noEmit --skipLibCheck 2>/dev/null || true

# 4. Execute exactly the new/changed specs
SPECS=$(echo "$CHANGED" | grep -E '\.spec\.(ts|js)$' || true)
if [ -n "$SPECS" ]; then
  npx playwright test $SPECS --reporter=json > "$REPORT_DIR/${KEY}-${TREPO}.json" || {
    echo "TESTS_FAILED"; exit 5; }
fi

# 5. Secret / PII pattern scan on the diff
git diff HEAD | grep -iE '(api[_-]?key|password|secret|token)\s*[:=]\s*["'"'"'][^"'"'"']+' && {
  echo "SECRET_PATTERN"; exit 3; } || true

# 6. Commit & push (branch protection blocks main; token scoped to branches)
git add -A
git commit -m "test(${KEY}): AI-generated E2E updates" \
  -m "Co-Authored-By: ai-qe-agent <ai-qe@company.com>"
git push origin HEAD
echo "COMMITTED $(git rev-parse --short HEAD)"
