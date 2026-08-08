#!/usr/bin/env bash
# Prompt-cache probe (cost-reduction story 4.1): MEASURE whether provider-side
# prompt caching engages on our prefix shape, instead of assuming it.
#
# The claude CLI manages caching itself — there is no public per-block
# cache-control flag — so the honest move is evidence: run the same cheap phase
# twice back-to-back in real mode and read the second run's
# cache_read_input_tokens share from the result JSON the pipeline already saves.
#
#   share ~0%   caching does not engage on our shape -> the documented fallback
#               (direct Messages API with explicit cache_control) becomes a
#               go/no-go decision worth its cost
#   share high  the cache-ordered prompt assembly is earning its keep; nothing
#               to build
#
# Needs a working `claude` CLI auth (same blocker as make parity-*; REVIEW.md
# item 5). Costs roughly two triage phases (~$0.02 at haiku rates).
set -euo pipefail
cd "$(dirname "$0")/.."

# Resolved, not compared to a literal. This guard is what stands between
# `make cache-probe` and two REAL triage runs against a paid provider, and
# `AIQE_MOCK=true` used to walk straight past it: the guard read "not 1" as
# "real mode", which is backwards for someone typing a truthy word. Verified —
# it proceeded and printed `input=0 cache_read=0`, zeros that look like a
# measurement of a cache that was never exercised.
_mock=1
case "$(printf '%s' "${AIQE_MOCK-1}" | tr 'A-Z' 'a-z')" in
  0|false|no|off) _mock=0 ;;
  1|true|yes|on)  _mock=1 ;;
  *) _mock=1; echo "WARNING: AIQE_MOCK='${AIQE_MOCK}' is not a recognized boolean" \
       "- assuming mock, so nothing is billed." >&2 ;;
esac
_real=0
case "$(printf '%s' "${AIQE_REAL_LLM-0}" | tr 'A-Z' 'a-z')" in
  1|true|yes|on) _real=1 ;;
esac
if [ "$_mock" = "1" ] && [ "$_real" != "1" ]; then
  echo "cache-probe needs real phases: run with AIQE_MOCK=0 (or AIQE_MOCK=1 AIQE_REAL_LLM=1"
  echo "for parity mode against the demo estate). Nothing was measured."
  exit 2
fi

# This script shares the pipeline's mutable scratch and durable spend path, so
# it must share the run lock as well.  Every real probe call is labelled before
# dispatch by run_phase.sh, metered by budget.py, and flushed on EXIT even when
# the second call fails.  The explicit attribution keeps measurements outside
# a user's task statement.
export RUN_ID="cache-probe-$(date +%s)-$$"
export MODE="probe" AIQE_RUN_MODE="probe"
export KEY="${KEY:-CACHE-PROBE}"
export AIQE_COST_ATTRIBUTION="probe"
STARTS_FILE="${AIQE_PHASE_STARTS_FILE:-out/phase-starts.jsonl}"
_probe_exit() {
  local rc="${1:-0}" flush_error=""
  if ! flush_error=$(python3 engine/lib/spend_ledger.py flush "$RUN_ID" \
      "$MODE" "$KEY" 2>&1); then
    echo "[cost-ledger] $flush_error" >&2
  fi
  rmdir out/.pipeline.lock 2>/dev/null || true
  return "$rc"
}
mkdir -p out
if ! mkdir out/.pipeline.lock 2>/dev/null; then
  echo "PIPELINE_BUSY: another run holds out/.pipeline.lock" >&2
  exit 75
fi
trap '_probe_exit "$?"' EXIT
rm -f "${AIQE_COST_LEDGER:-out/cost.tsv}" "$STARTS_FILE"

probe() {  # $1=ledger label; prints "input cache_read" from the result JSON
  local label="$1" rc=0
  rm -f "out/$label.json" "out/$label.contract.json"
  AIQE_PHASE_LABEL="$label" bash engine/phases/run_phase.sh triage \
    prompts/pr-triage.md workspace AGENTS.md > /dev/null || rc=$?
  python3 engine/lib/budget.py record "$label" "out/$label.json" "$rc" || true
  [ "$rc" -eq 0 ] || return "$rc"
  python3 - "$label" <<'PY'
import json
import sys
u = (json.load(open(f"out/{sys.argv[1]}.json", encoding="utf-8")).get("usage") or {})
print(u.get("input_tokens", 0), u.get("cache_read_input_tokens", 0))
PY
}

export AIQE_PHASE_CACHE=0   # the CONTENT cache would hide the second call entirely
echo "probe 1/2 (cold)..."
read -r IN1 CR1 <<< "$(probe cache-probe-cold)"
echo "probe 2/2 (warm, immediately after)..."
read -r IN2 CR2 <<< "$(probe cache-probe-warm)"

python3 - "$IN1" "$CR1" "$IN2" "$CR2" <<'PY'
import sys
in1, cr1, in2, cr2 = map(int, sys.argv[1:5])
den = in2 + cr2
share = (cr2 / den * 100) if den else 0.0
print(f"cold:  input={in1}  cache_read={cr1}")
print(f"warm:  input={in2}  cache_read={cr2}  -> cache-read share {share:.0f}%")
if share >= 50:
    print("VERDICT: provider caching engages on our prefix shape — the")
    print("cache-ordered assembly is earning the discount. Nothing to build.")
elif den:
    print("VERDICT: little/no cache engagement — evaluate the documented")
    print("fallback (direct Messages API with explicit cache_control blocks);")
    print("see docs/cost-reduction-architecture.md §4.1 for the go/no-go.")
else:
    print("VERDICT: no usage data returned — check the CLI auth and out/triage.json")
PY
