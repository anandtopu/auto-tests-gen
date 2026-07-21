#!/usr/bin/env bash
# Deterministic Quality Gate (architecture §5.5) — the ONLY place push happens.
# Runs INSIDE one writable test repo. Usage: gate.sh <KEY> <test_repo_name>
# Framework-agnostic: lint/test commands come from the repo's .ai-qe/config.yaml.
set -euo pipefail
KEY=${1:?key}; TREPO=${2:?test_repo_name}
ROOT="${AIQE_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
REPORT_DIR="$ROOT/reports"; mkdir -p "$REPORT_DIR"

# Safety: the gate must run inside a standalone test repo — never the scaffold's own
# repository (a clone missing .git makes git commands resolve to the parent repo).
TOP=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$TOP" ] || [ "$TOP/.git" -ef "$ROOT/.git" ]; then
  echo "GATE_REFUSED: cwd is not a standalone test repo"; exit 6
fi
CFG=".ai-qe/config.yaml"
LINT_CMD=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['commands']['lint'])")
TEST_CMD=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['commands']['test'])")

CHANGED=$(git diff --name-only HEAD; git ls-files --others --exclude-standard)
CHANGED=$(echo "$CHANGED" | sed '/^$/d')
[ -z "$CHANGED" ] && { echo "GATE_STATUS=NO_CHANGES"; exit 0; }

# 1. Scope: only test-repo content + catalog sidecars + repo config
if echo "$CHANGED" | grep -vE '^(tests/|suites/|fixtures/|data/|pages/|catalog/|\.ai-qe/)' ; then
  echo "SCOPE_VIOLATION"; exit 2
fi

# 2. Born-mapped: every new spec has a catalog sidecar entry
NEW_SPECS=$(echo "$CHANGED" | grep -E '\.spec\.(ts|js)$' || true)
for spec in $NEW_SPECS; do
  git ls-files --error-unmatch "$spec" >/dev/null 2>&1 && continue   # existing (modified) spec
  grep -q "$spec" catalog/*.jsonl 2>/dev/null || { echo "UNMAPPED_TEST: $spec"; exit 4; }
done

# 3. Static checks
bash -c "$LINT_CMD"

# 4. Execute exactly the new/changed specs, inside the provisioned environment
SPECS=$(echo "$CHANGED" | grep -E '\.spec\.(ts|js)$' || true)
if [ -n "$SPECS" ]; then
  bash "$ROOT/bin/with-env.sh" . -- bash -c "$TEST_CMD $SPECS" \
    > "$REPORT_DIR/${KEY}-${TREPO}.log" 2>&1 || { echo "TESTS_FAILED"; tail -5 "$REPORT_DIR/${KEY}-${TREPO}.log"; exit 5; }
fi

# 5. Secret / PII pattern scan on new content
{ git diff HEAD; git ls-files --others --exclude-standard -z | xargs -0 -r cat; } \
  | grep -iE '(api[_-]?key|password|secret|token)\s*[:=]\s*["'"'"'][^"'"'"']+' \
  && { echo "SECRET_PATTERN"; exit 3; } || true

# 6. Commit & push (branch protection blocks main; token scoped to branches)
git add -A
git commit -qm "test(${KEY}): AI-generated E2E updates" \
  -m "Co-Authored-By: ai-qe-agent <ai-qe@company.com>"
git push origin HEAD 2>/dev/null || echo "PUSH_SKIPPED (no remote — demo mode)"
echo "GATE_STATUS=COMMITTED $(git rev-parse --short HEAD)"
