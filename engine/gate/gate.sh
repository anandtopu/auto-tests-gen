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
# The gate EXECUTES commands/1 from this file, so it must never read a version
# an LLM phase could have authored in THIS run. Two independent guards:
#   (1) `.ai-qe/` is off the writable scope list below — a run that touched it
#       is a SCOPE_VIOLATION, so the injection cannot even be introduced;
#   (2) the commands come from the COMMITTED file, never the working tree, so
#       a modification arriving by any other route still cannot steer this run.
# Without these, `commands.lint` was arbitrary code the gate ran with its own
# authority — and the gate is the component that holds the push credential.
CFG_YAML=$(git show "HEAD:$CFG" 2>/dev/null) || {
  echo "GATE_REFUSED: $CFG is not committed — the gate will not take its"
  echo "  lint/test commands from an uncommitted config. Commit it first."
  exit 6; }
_cmd() {
  printf '%s' "$CFG_YAML" | python3 -c \
    "import sys,yaml;print((yaml.safe_load(sys.stdin) or {})['commands']['$1'])"
}
LINT_CMD=$(_cmd lint)
TEST_CMD=$(_cmd test)

CHANGED=$(git diff --name-only HEAD; git ls-files --others --exclude-standard)
CHANGED=$(echo "$CHANGED" | sed '/^$/d')
[ -z "$CHANGED" ] && { echo "GATE_STATUS=NO_CHANGES"; exit 0; }

# 1. Scope: only test-repo content + catalog sidecars + repo config.
# Filenames are LLM-authored and later interpolated into shell commands — restrict
# to a safe charset so a crafted name (e.g. `$(...)`) can never be shell-evaluated.
if echo "$CHANGED" | grep -qE '[^A-Za-z0-9._/-]'; then
  echo "SCOPE_VIOLATION (unsafe characters in filename)"; exit 2
fi
# `.ai-qe/` is deliberately NOT on this list. It holds the lint/test commands
# the gate executes, so letting a run rewrite it turns the repo's own config
# into an injection vector against the one component that holds push rights.
# Repo config is changed by its owner (bin/onboard.sh), out of band — no
# pipeline phase has ever needed to write it.
if echo "$CHANGED" | grep -vE '^(tests/|suites/|fixtures/|data/|pages/|catalog/)' ; then
  echo "SCOPE_VIOLATION"; exit 2
fi

# 2. Born-mapped: every new spec has a catalog sidecar entry
NEW_SPECS=$(echo "$CHANGED" | grep -E '\.spec\.(ts|js)$' || true)
for spec in $NEW_SPECS; do
  git ls-files --error-unmatch "$spec" >/dev/null 2>&1 && continue   # existing (modified) spec
  # Fixed-string, quote-delimited match: the path must appear as a complete JSON
  # string value ("file" field). A plain `grep -q "$spec"` would treat the dots as
  # regex wildcards and accept any superstring/mention of the path.
  grep -qF "\"$spec\"" catalog/*.jsonl 2>/dev/null || { echo "UNMAPPED_TEST: $spec"; exit 4; }
done

# 2b. Spec satisfaction (SDD 3.2, org-config spec.enforce off|warn|strict).
# Ordered after born-mapped: every approved scenario covered-or-waived, no
# forged/stale scenario ids. off = absent; warn = printed; strict = exit 8.
# Exempt by construction for keys without an approved structured spec.
echo "$CHANGED" > "$ROOT/out/gate-changed-${TREPO}.txt"
SPEC_RC=0
python3 "$ROOT/engine/gate/spec_check.py" "$KEY" "$TREPO" \
  "$ROOT/out/gate-changed-${TREPO}.txt" || SPEC_RC=$?
[ "$SPEC_RC" -eq 0 ] || { echo "SPEC_UNSATISFIED"; exit 8; }

# 3. Static checks
bash -c "$LINT_CMD"

# 4. Execute exactly the new/changed specs, inside the provisioned environment
# (single line — a newline-separated list would make `bash -c` execute file 2+ as commands)
SPECS=$(echo "$CHANGED" | grep -E '\.spec\.(ts|js)$' | tr '\n' ' ' || true)
if [ -n "$SPECS" ]; then
  bash "$ROOT/bin/with-env.sh" . -- bash -c "$TEST_CMD $SPECS" \
    > "$REPORT_DIR/${KEY}-${TREPO}.log" 2>&1 || { echo "TESTS_FAILED"; tail -5 "$REPORT_DIR/${KEY}-${TREPO}.log"; exit 5; }
fi

# 5. Secret / PII pattern scan on new content (capture-then-test: under pipefail a
# failing left-hand stage must not discard a grep match via the trailing || true)
FOUND=$({ git diff HEAD; git ls-files --others --exclude-standard -z | xargs -0 -r cat; } \
  | grep -iE '(api[_-]?key|password|secret|token)\s*[:=]\s*["'"'"'][^"'"'"']+' || true)
if [ -n "$FOUND" ]; then echo "SECRET_PATTERN"; exit 3; fi

# Check-only: every check above has run; stop before writing anything. Used by the
# OpenHands Stop hook so an agent is told its work would be rejected BEFORE it
# declares the task done — without granting the agent commit authority. The gate
# remains the only thing that ever commits or pushes.
if [ "${AIQE_GATE_CHECK_ONLY:-0}" = "1" ]; then
  echo "GATE_STATUS=WOULD_COMMIT"; exit 0
fi

# 6. Commit & push (branch protection blocks main; token scoped to branches)
git add -A
git commit -qm "test(${KEY}): AI-generated E2E updates" \
  -m "Co-Authored-By: ai-qe-agent <ai-qe@company.com>"
# A real push failure (auth, protection, network) must NOT be reported as success;
# only the no-remote demo case is skippable.
if git remote get-url origin >/dev/null 2>&1; then
  git push origin HEAD || { echo "PUSH_FAILED"; exit 7; }
else
  echo "PUSH_SKIPPED (no remote — demo mode)"
fi
echo "GATE_STATUS=COMMITTED $(git rev-parse --short HEAD)"
