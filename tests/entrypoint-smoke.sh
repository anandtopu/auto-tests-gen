#!/usr/bin/env bash
# Adversarial UAT for FIRST-BOOT STATE SEEDING (R12, bin/container-entrypoint.sh).
#
# This script decides whether a NEW deployment has an estate at all. If it seeds
# nothing, the container comes up with no catalog mappings and no repo registry,
# so every routing decision resolves to nothing — the platform's signature
# silent failure, at the one moment nobody is watching a log.
#
# It was the only entry point in the repo that nothing referenced: not a test,
# not the Makefile, only the Dockerfile's ENTRYPOINT. Running it once found that
# on a completely empty state root it seeded ZERO files and reported "state root
# already populated — nothing seeded".
#
#   1 first boot     an empty state root receives the estate
#   2 data only      no code, no bytecode, no derived/generated content
#   3 the volume wins  a second boot never overwrites a human's edits
#   4 empty != seeded  "nothing copied" and "already populated" are different
#                      facts, and an empty root that got nothing must be loud
#   5 broken plan    a failing seed-plan must REFUSE to start, not boot bare
#   6 passthrough    no AIQE_STATE_DIR means no relocation and no seeding
#
# Run: make test-entrypoint
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
# The transaction log is REDIRECTED for this suite. Nothing set AIQE_EVENTS_DIR,
# so every emit reached the estate's REAL audit log — the record an operator
# reads to see what happened here, and the input `make maintain` feeds to
# alert_rules.evaluate(), which counts events in a window and DELIVERS through
# the Notify port. A test suite could page somebody with its own traffic.
# Absolute + native: python resolves this variable, and a subprocess that has
# cd'd into a workspace checkout must not create a stray log there.
mkdir -p "$ROOT/out/test-events"
export AIQE_EVENTS_DIR="$(cd "$ROOT/out/test-events" && pwd -W 2>/dev/null || pwd)"
fail=0
TMPD_POSIX=$(mktemp -d); trap 'rm -rf "$TMPD_POSIX"' EXIT
# Native form for AIQE_STATE_DIR (python resolves it); POSIX form for PATH
# shadowing. See tests/bootstrap-smoke.sh for why both are needed.
TMPD=$(cd "$TMPD_POSIX" && pwd -W 2>/dev/null || printf '%s' "$TMPD_POSIX")
check() { if [ "$1" = "$2" ]; then echo "PASS $3"; else echo "FAIL $3 ($2, want $1)"; fail=1; fi; }

EP="bin/container-entrypoint.sh"

# --- 1. first boot populates an empty state root ---------------------------
S1="$TMPD/s1"
APP_HOME="$ROOT" AIQE_STATE_DIR="$S1" bash "$EP" true >"$TMPD/1.out" 2>&1
rc=$?
check 0 "$rc" "first boot exits 0 (see $TMPD/1.out)"

files=$(cd "$TMPD_POSIX/s1" 2>/dev/null && find . -type f | wc -l || echo 0)
[ "${files:-0}" -gt 0 ] && r=ok || r="files=$files"
check ok "$r" "an empty state root receives the estate ($files files)"

for must in catalog/e2e-api-tests-1.jsonl registry/repo-registry.yaml; do
  [ -f "$TMPD_POSIX/s1/$must" ] && r=ok || r="missing $must"
  check ok "$r" "seeded $must"
done

# --- 2. DATA ONLY ----------------------------------------------------------
# The rule stated at the top of the entrypoint, enforced against what a boot
# ACTUALLY copied rather than against how the SEEDED list reads. Seeding
# `catalog/review` as a directory dragged in export_review_queue.py and a
# __pycache__; seeding `knowledge/facts` dragged in the derived tier, which is
# gitignored, rebuilt by `make repo-facts`, and excluded from the state bundle
# precisely because it regenerates.
bad=$(cd "$TMPD_POSIX/s1" && find . \( -name '*.py' -o -name '*.pyc' -o -name '*.pyo' \
      -o -name '*.sh' -o -path '*__pycache__*' -o -path '*/derived/*' \) 2>/dev/null | sort | tr '\n' ' ')
[ -z "$bad" ] && r=ok || r="seeded: $bad"
check ok "$r" "no code, bytecode or derived content reaches the volume"

# Generated paths must arrive EMPTY, never restored from the image.
for gen in testplans testdata specs; do
  n=$(cd "$TMPD_POSIX/s1/$gen" 2>/dev/null && find . -type f | wc -l || echo 0)
  [ "${n:-0}" -eq 0 ] && r=ok || r="$gen has $n file(s)"
  check ok "$r" "$gen/ is created empty, not seeded from the image"
done

# --- 3. the volume wins ----------------------------------------------------
# Once a human has edited state, a routine pod restart must never revert it.
echo "EDITED BY A HUMAN" > "$TMPD_POSIX/s1/registry/repo-registry.yaml"
APP_HOME="$ROOT" AIQE_STATE_DIR="$S1" bash "$EP" true >"$TMPD/3.out" 2>&1
got=$(cat "$TMPD_POSIX/s1/registry/repo-registry.yaml")
check "EDITED BY A HUMAN" "$got" "a second boot never overwrites existing state"
grep -q "already populated" "$TMPD/3.out" && r=ok || r="did not report already-populated"
check ok "$r" "a populated state root says so"

# A PARTIALLY populated volume still receives the missing pieces — a restart
# after someone deleted a file, or a path added by a later image.
rm -f "$TMPD_POSIX/s1/catalog/e2e-api-tests-1.jsonl"
APP_HOME="$ROOT" AIQE_STATE_DIR="$S1" bash "$EP" true >"$TMPD/3b.out" 2>&1
[ -f "$TMPD_POSIX/s1/catalog/e2e-api-tests-1.jsonl" ] && r=ok || r=missing
check ok "$r" "a missing file is re-seeded without touching the rest"
got=$(cat "$TMPD_POSIX/s1/registry/repo-registry.yaml")
check "EDITED BY A HUMAN" "$got" "re-seeding one file leaves the others alone"

# --- 4. empty and unseeded is not "already populated" ----------------------
# The original defect. An image with no seed content must not report a normal
# boot — those are different facts and they lead to opposite actions
# (constitution C13).
BARE="$TMPD/bare"
mkdir -p "$TMPD_POSIX/bare/engine/lib"
cp "$ROOT/engine/lib/app_paths.py" "$TMPD_POSIX/bare/engine/lib/"
APP_HOME="$BARE" AIQE_STATE_DIR="$TMPD/s4" bash "$EP" true >"$TMPD/4.out" 2>&1
rc=$?
grep -qi "WARNING" "$TMPD/4.out" && ! grep -q "already populated" "$TMPD/4.out" && r=ok \
  || r="rc=$rc; said: $(tr '\n' ' ' < "$TMPD/4.out" | cut -c1-90)"
check ok "$r" "an empty root that received nothing warns, never 'already populated'"

# --- 5. a broken seed plan must refuse to start ----------------------------
# Process substitution discarded the producer's exit status, so a crashing
# app_paths read exactly like "there is nothing to seed" and the container
# booted with an empty estate.
BROKEN="$TMPD/broken"
mkdir -p "$TMPD_POSIX/broken/engine/lib"
printf 'import sys\nsys.exit(9)\n' > "$TMPD_POSIX/broken/engine/lib/app_paths.py"
APP_HOME="$BROKEN" AIQE_STATE_DIR="$TMPD/s5" bash "$EP" true >"$TMPD/5.out" 2>&1
rc=$?
[ "$rc" -ne 0 ] && grep -q "FATAL" "$TMPD/5.out" && r=ok || r="rc=$rc"
check ok "$r" "a failing seed plan refuses to start rather than boot bare"

# --- 6. no relocation configured -> straight through -----------------------
# `docker run` with no AIQE_STATE_DIR must behave exactly as before R12.
out=$(APP_HOME="$ROOT" bash "$EP" echo MARKER 2>&1)
[ "$out" = "MARKER" ] && r=ok || r="$out"
check ok "$r" "no AIQE_STATE_DIR execs through without seeding or output"

# The command is exec'd, so its exit status must be the container's — on BOTH
# paths. The first version only covered passthrough, and a mutation that broke
# `exec` on the SEEDED path sailed through: an orchestrator restarts on a
# non-zero exit, so a swallowed status turns a crashed dashboard into a
# container that looks alive and serves nothing.
APP_HOME="$ROOT" bash "$EP" sh -c 'exit 42' >/dev/null 2>&1
check 42 "$?" "the wrapped exit status survives (no relocation)"

APP_HOME="$ROOT" AIQE_STATE_DIR="$TMPD/s6" bash "$EP" sh -c 'exit 43' >/dev/null 2>&1
check 43 "$?" "the wrapped exit status survives (after seeding)"

echo
[ "$fail" -eq 0 ] && echo "entrypoint smoke: all checks passed" \
                  || echo "entrypoint smoke: FAILURES above"
exit "$fail"
