#!/usr/bin/env bash
# Deterministic Quality Gate (architecture §5.5) — the ONLY place push happens.
# Runs INSIDE one writable test repo. Usage: gate.sh <KEY> <test_repo_name>
# Framework-agnostic: lint/test commands come from the repo's .ai-qe/config.yaml.
set -euo pipefail
KEY=${1:?key}; TREPO=${2:?test_repo_name}
# Both arguments are interpolated into a path ($REPORT_DIR/${KEY}-${TREPO}.log)
# and into the commit message, so they are validated HERE rather than trusted to
# have been validated by whoever called. pipeline.sh does check its KEY at entry,
# but it is not the only caller: .openhands/hooks/gate-check.sh takes KEY from
# the environment, and anything invoking the gate directly supplies both. A
# guard that exists in one branch and not its sibling is how a guard gets lost
# (the same reasoning as R4 in CLAUDE.md), and this is the component holding the
# push credential. Charset matches pipeline.sh's exactly so the two cannot
# disagree about what a key is; `..` is rejected separately because it PASSES
# that charset while still being a path component that escapes.
for _arg in "$KEY" "$TREPO"; do
  case "$_arg" in
    ""|*[!A-Za-z0-9._-]*)
      echo "INVALID_ARG: '$_arg' — key and test-repo name must be 1+ characters"
      echo "  from [A-Za-z0-9._-]. Nothing ran."; exit 64;;
    .|..)
      echo "INVALID_ARG: '$_arg' is a path component, not a name. Nothing ran."
      exit 64;;
  esac
done
unset _arg
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
# A missing or misspelt command key used to surface as a raw Python KeyError
# traceback, and `set -e` then aborted with exit 1 — a code that appears
# NOWHERE in this gate's documented protocol, from the component that holds the
# push credential. Measured: `commands.test` mistyped as `tests` printed
# "KeyError: 'test'" and named neither the file nor the fix. It already failed
# CLOSED, which is right; what was missing is the sentence. Exit 6 is the
# existing GATE_REFUSED code and the sibling case (a config that is not
# committed) already uses it, so this adds no new code to the protocol.
_cmd() {
  printf '%s' "$CFG_YAML" | python3 -c '
import sys, yaml
key, cfg = sys.argv[1], sys.argv[2]
try:
    doc = yaml.safe_load(sys.stdin) or {}
except Exception as exc:
    sys.exit(f"GATE_REFUSED: {cfg} is committed but is not valid YAML ({exc}). "
             f"The gate will not guess the commands it executes.")
if not isinstance(doc, dict):
    sys.exit(f"GATE_REFUSED: {cfg} is not a mapping, so it declares no "
             f"commands. The gate will not guess the commands it executes.")
cmds = doc.get("commands")
if not isinstance(cmds, dict):
    sys.exit(f"GATE_REFUSED: {cfg} has no `commands:` section, so there is no "
             f"`{key}` command to run. Add commands.lint and commands.test.")
value = cmds.get(key)
if not isinstance(value, str) or not value.strip():
    sys.exit(f"GATE_REFUSED: {cfg} declares no `commands.{key}`. It has: "
             f"{sorted(cmds)} — check the spelling. Running no {key} step is "
             f"not the same as passing it, so the gate refuses rather than "
             f"committing tests nothing verified.")
print(value)
' "$1" "$CFG"
}
LINT_CMD=$(_cmd lint) || exit 6
TEST_CMD=$(_cmd test) || exit 6

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

# What counts as a generated test file. `.spec.(ts|js)` ALONE was a hole: a file
# named `foo.test.ts` was neither required to have a catalog sidecar nor
# executed, yet `git add -A` committed and pushed it. Playwright's default
# testMatch is `**/*.@(spec|test).[jt]s`, and this platform already treats all
# four suffixes as first-class elsewhere (testcase_learning.SPEC_SUFFIXES,
# spec_exemplars' "a mature .test.js estate must not be misread"). So for any
# onboarded repo using .test.ts naming — a configuration explicitly supported —
# the constitution's "every generated spec must be born-mapped or the gate
# rejects it" was simply false, and nothing executed the tests before pushing.
SPEC_RE='\.(spec|test)\.(ts|js)$'

# 2. Born-mapped: every new spec has a catalog sidecar entry
NEW_SPECS=$(echo "$CHANGED" | grep -E "$SPEC_RE" || true)
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
# 8 is spec_check's OWN verdict (EXIT_SPEC): an approved scenario is uncovered
# and unwaived. Anything else is the checker failing to run — a missing
# python3 (127), an ImportError, a bad argv — and reporting that as
# SPEC_UNSATISFIED sends an operator to fix a spec that is fine, which is the
# C13 shape in the component whose verdict decides whether code is pushed.
# Both still fail CLOSED; only the reason differs.
if [ "$SPEC_RC" -eq 8 ]; then
  echo "SPEC_UNSATISFIED"; exit 8
elif [ "$SPEC_RC" -ne 0 ]; then
  echo "SPEC_CHECK_FAILED (spec_check.py exited $SPEC_RC — the CHECK malfunctioned;"
  echo "  this is not a finding about the spec). Nothing was committed."
  exit 11
fi

# 3. Static checks. The linter's exit status must NOT become the gate's own:
# under `set -e` it did, so eslint exiting 2 (its fatal-config code) was read
# downstream as SCOPE_VIOLATION, 3 as SECRET_PATTERN, 4 as UNMAPPED_TEST. Every
# other check emits a distinct token; this one had none, so a lint problem was
# reported as a specific, different, established fact about the generated tests.
LINT_RC=0
bash -c "$LINT_CMD" || LINT_RC=$?
[ "$LINT_RC" -eq 0 ] || {
  echo "LINT_FAILED (exit $LINT_RC from the repo's own commands.lint)"
  echo "  This is the LINTER's status, not a scope/secret/mapping finding."
  exit 9; }

# 4. Execute exactly the new/changed specs, inside the provisioned environment
# (single line — a newline-separated list would make `bash -c` execute file 2+ as commands)
SPECS=$(echo "$CHANGED" | grep -E "$SPEC_RE" | tr '\n' ' ' || true)
if [ -n "$SPECS" ]; then
  RUN_RC=0
  bash "$ROOT/bin/with-env.sh" . -- bash -c "$TEST_CMD $SPECS" \
    > "$REPORT_DIR/${KEY}-${TREPO}.log" 2>&1 || RUN_RC=$?
  if [ "$RUN_RC" -ne 0 ]; then
    # "The app under test never started" is not "the generated tests failed".
    # with-env.sh emits APP_REPO_NOT_FOUND / APP_START_FAILED and exits 8 / 7,
    # and all of it was flattened into TESTS_FAILED — sending a human to
    # debug tests when the environment never came up. Match on the MARKER
    # rather than the code alone: with-env passes the test command's own status
    # through, so a test that happens to exit 7 must not be misread as a
    # provisioning failure.
    if grep -qE '^(APP_REPO_NOT_FOUND|APP_START_FAILED)' "$REPORT_DIR/${KEY}-${TREPO}.log"; then
      echo "ENV_PROVISION_FAILED (the app under test never came up; the generated"
      echo "  tests were never executed, so nothing is known about them)"
      tail -5 "$REPORT_DIR/${KEY}-${TREPO}.log"
      exit 10
    fi
    echo "TESTS_FAILED"; tail -5 "$REPORT_DIR/${KEY}-${TREPO}.log"; exit 5
  fi
fi

# 5. Secret / PII pattern scan on new content (capture-then-test: under pipefail a
# failing left-hand stage must not discard a grep match via the trailing || true)
#
# Read the FILES, never `git diff`. `git diff HEAD` honours .gitattributes from
# the WORKING TREE, so a run that writes `tests/.gitattributes` containing
# `* -diff` makes the diff emit "Binary files a/x and b/x differ" — no content —
# and the scan then sees nothing. Verified: that pair (a .gitattributes plus a
# secret appended to an already-tracked spec) passes scope and charset, needs no
# catalog sidecar, and the secret is committed and pushed on a SINGLE run. The
# untracked half was never affected because it already `cat`s files raw; this
# makes the tracked half behave the same way.
#
# Reading whole files also scans MORE than the diff did: a secret already
# present in an untouched region of a modified file is now caught too.
CHANGED_TRACKED=$(git diff --name-only HEAD || true)
FOUND=$({ [ -n "$CHANGED_TRACKED" ] && printf '%s\n' "$CHANGED_TRACKED" \
            | tr '\n' '\0' | xargs -0 -r cat 2>/dev/null
          git ls-files --others --exclude-standard -z | xargs -0 -r cat 2>/dev/null; } \
  | grep -iE '(api[_-]?key|password|secret|token)\s*[:=]\s*["'"'"'][^"'"'"']+' || true)
if [ -n "$FOUND" ]; then echo "SECRET_PATTERN"; exit 3; fi

# Check-only: every check above has run; stop before writing anything. Used by the
# OpenHands Stop hook so an agent is told its work would be rejected BEFORE it
# declares the task done — without granting the agent commit authority. The gate
# remains the only thing that ever commits or pushes.
# Only the literal `1` used to mean check-only, so AIQE_GATE_CHECK_ONLY=true
# fell through and the gate COMMITTED AND PUSHED for an operator who asked for a
# dry run. Here the safe direction is the opposite of AIQE_MOCK's: an
# unrecognized value must mean check-only, because the two outcomes are "a run
# that wrote nothing" and "a commit pushed to somebody's repository".
# `-0` not `:-0`, so an EMPTY value is distinguishable from an unset one and
# falls to the warning branch. Unset means commit (the gate's normal job), but
# `AIQE_GATE_CHECK_ONLY=` is someone trying to say something; if they meant a
# dry run, treating it as commit pushes to a real repository, while treating it
# as check-only costs them a warning and a re-run.
case "$(printf '%s' "${AIQE_GATE_CHECK_ONLY-0}" | tr 'A-Z' 'a-z')" in
  0|false|no|off) ;;                                   # commit for real
  1|true|yes|on)  echo "GATE_STATUS=WOULD_COMMIT"; exit 0 ;;
  *) echo "WARNING: AIQE_GATE_CHECK_ONLY='${AIQE_GATE_CHECK_ONLY}' is not a" \
          "recognized boolean (1/true/yes/on or 0/false/no/off) — treating as" \
          "CHECK-ONLY. Nothing was committed or pushed." >&2
     echo "GATE_STATUS=WOULD_COMMIT"; exit 0 ;;
esac

# 5b. RE-CHECK SCOPE AGAINST THE TREE WE ARE ABOUT TO COMMIT.
#
# Every check above ran against $CHANGED, computed at line 36 — BEFORE step 4
# executed LLM-authored test code. `git add -A` stages whatever exists now. So a
# generated spec that writes a file while it runs was never scope-checked, never
# charset-checked and never born-mapped, yet gets committed and pushed.
#
# The worst instance is self-perpetuating: a spec that writes `.ai-qe/config.yaml`
# during execution gets it committed, and the NEXT run in that repo reads its
# lint/test commands from `git show HEAD:` — the "commands come from the
# COMMITTED config" guard — and executes the attacker's command with the
# credential that holds push rights. Both documented defences ("`.ai-qe/` is off
# the writable scope" and "commands are read from the committed config") are
# defeated by the same primitive, because one is enforced too early and the
# other trusts what the first one let through.
#
# Re-checking here is cheap and closes the window: the tree is inspected at the
# last possible moment, immediately before it becomes a commit.
FINAL=$(git diff --name-only HEAD; git ls-files --others --exclude-standard)
FINAL=$(echo "$FINAL" | sed '/^$/d')
if echo "$FINAL" | grep -qE '[^A-Za-z0-9._/-]'; then
  echo "SCOPE_VIOLATION (unsafe characters in filename, appeared during execution)"
  exit 2
fi
if echo "$FINAL" | grep -vE '^(tests/|suites/|fixtures/|data/|pages/|catalog/)' ; then
  echo "SCOPE_VIOLATION (path appeared during test execution)"; exit 2
fi
# Born-mapped, re-applied: a spec that materialised during execution must carry a
# sidecar exactly as one written by the generate phase does.
for spec in $(echo "$FINAL" | grep -E "$SPEC_RE" || true); do
  git ls-files --error-unmatch "$spec" >/dev/null 2>&1 && continue
  grep -qF "\"$spec\"" catalog/*.jsonl 2>/dev/null || {
    echo "UNMAPPED_TEST $spec (appeared during test execution)"; exit 4; }
done

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
