#!/usr/bin/env bash
# Core pipeline entry (Path 1/2/3 all call this). architecture §5.3, §5.8
# Usage: pipeline.sh pr <source_repo> <pr_number> | pipeline.sh jira <KEY>
#        pipeline.sh plan  <KEY> | <source_repo> <pr_number>
#                                  author the test plan ONLY, then stop for human
#                                  review/edit/approval (no test code, no commit)
#        pipeline.sh tests <KEY>   resume from an APPROVED plan: data -> generate ->
#                                  validate -> gate
set -euo pipefail
MODE=${1:?pr|jira|plan|tests}; export AIQE_ROOT="$PWD"; mkdir -p out workspace
# Validate — MODE is interpolated into python -c strings below, and an unknown
# mode would silently take the jira branch.
case "$MODE" in pr|jira|plan|tests|requirements) ;; *) echo "INVALID_MODE: $MODE (pr|jira|plan|tests|requirements)"; exit 64 ;; esac
# SDD 2.2: `requirements <KEY>` runs the chain through analyze, persists the
# EARS requirements spec, and STOPS for human validation — the stage before
# planning, mirroring plan-first. Re-running it deliberately re-authors (the
# env override lets the fresh analysis replace an approved file).
if [ "$MODE" = "requirements" ]; then export AIQE_REQUIREMENTS_REAUTHOR=1; fi
# The budget envelope (5.2) and degradation ladder (5.3) resolve per workflow.
export AIQE_RUN_MODE="$MODE"
# R12: mutable state may live outside the checkout (AIQE_STATE_DIR / per-path
# knobs) so the root filesystem can be read-only. Resolve ONCE here — python is
# the single source of truth for the precedence rules, bash never re-implements
# them — and export so phases and the mock harness inherit the same answers.
# With nothing configured every value is today's relative path, unchanged.
eval "$(python3 engine/lib/app_paths.py --sh)"
export AIQE_P_TESTPLANS AIQE_P_TESTDATA AIQE_P_SPECS AIQE_P_CATALOG
export AIQE_P_KNOWLEDGE AIQE_P_AGENTS AIQE_P_REGISTRY AIQE_P_SKILLS
# Config layers, lowest first: aiqe.properties < .env < explicit environment.
# Both emitters print `export K='v'` lines ONLY for keys absent from the
# environment, so an explicitly-exported variable always wins (the file can never
# invert a caller's AIQE_MOCK), and — unlike the old `source .env` — every value
# is EXPORTED, so adapters, phases and python children actually see it.
# .env is applied FIRST: first-fill wins, so .env beats the properties baseline
# (the Settings page writes .env — a UI save must never be masked by properties).
eval "$(python3 engine/lib/props_file.py dotenv-defaults 2>/dev/null || true)"
eval "$(python3 engine/lib/props_file.py shell-defaults 2>/dev/null || true)"
# Map AIQE_* proxy vars to standard env vars so curl (adapters) and Python urllib
# both pick them up automatically — including NO_PROXY bypass for internal hosts.
if [ -n "${AIQE_HTTPS_PROXY:-}" ]; then
  export HTTPS_PROXY="${HTTPS_PROXY:-$AIQE_HTTPS_PROXY}"
  export HTTP_PROXY="${HTTP_PROXY:-$AIQE_HTTPS_PROXY}"
  export https_proxy="${https_proxy:-$AIQE_HTTPS_PROXY}"
  export http_proxy="${http_proxy:-$AIQE_HTTPS_PROXY}"
fi
if [ -n "${AIQE_NO_PROXY:-}" ]; then
  export NO_PROXY="${NO_PROXY:-$AIQE_NO_PROXY}"
  export no_proxy="${no_proxy:-$AIQE_NO_PROXY}"
fi

# Run isolation: workspace/ and out/ are shared scratch, so one run at a time per
# checkout (parallel capacity = one sandbox/checkout per run, e.g. OpenHands).
# Waits up to 2 min; breaks locks older than 90 min (crashed holder — threshold
# sits above the longest real-LLM phase chain so a live run is never broken).
LOCK=out/.pipeline.lock
# `mkdir` cannot tell "another run holds it" from "this filesystem is read-only"
# — both simply fail, and stderr is discarded. Under readOnlyRootFilesystem that
# made the loop below spin its full 120 seconds and then report PIPELINE_BUSY,
# sending an operator to hunt a concurrent run that does not exist. Same defect
# class as review C3 (a Windows PENDING-DELETE raising PermissionError where the
# code expected FileExistsError): a distinguishable failure reported as the
# wrong one. Found by running the image with `--read-only` (R12). Check once, up
# front, and fail fast with the actual reason.
mkdir -p "$(dirname "$LOCK")" 2>/dev/null || true
if [ ! -w "$(dirname "$LOCK")" ]; then
  echo "PIPELINE_UNWRITABLE: $(dirname "$LOCK") is not writable, so $LOCK cannot be taken."
  echo "  This is NOT lock contention — check the volume mount, filesystem and ownership."
  exit 75
fi
ACQUIRED=0
for i in $(seq 1 120); do
  if mkdir "$LOCK" 2>/dev/null; then ACQUIRED=1; break; fi
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +90 2>/dev/null)" ]; then rmdir "$LOCK" 2>/dev/null || true; fi
  sleep 1
done
if [ "$ACQUIRED" != "1" ]; then echo "PIPELINE_BUSY: another run holds $LOCK"; exit 75; fi
# One EXIT handler owns both durable spend and the run lock. Bash traps replace
# rather than stack, so adding a second trap would strand the lock. Capture the
# incoming status, flush first, release second, and return the original status.
_pipeline_exit() {
  local rc="${1:-0}" flush_error=""
  if [ -n "${RUN_ID:-}" ]; then
    if ! flush_error=$(python3 engine/lib/spend_ledger.py flush "$RUN_ID" \
        "${MODE:-}" "${KEY:-}" 2>&1); then
      echo "[cost-ledger] $flush_error" >&2
      if declare -F EV >/dev/null 2>&1; then
        EV cost.ledger_failed "${KEY:-?}" degraded "run_id=$RUN_ID"
      fi
    fi
  fi
  rmdir "$LOCK" 2>/dev/null || true
  return "$rc"
}
trap '_pipeline_exit "$?"' EXIT
# An unrecognized NOTIFY_KIND used to fall through the `*)` arm to slack, so a
# typo'd `emails` delivered nothing to the address it was configured for and
# said nothing about it. A notification channel that silently is not the one you
# chose is worse than one that is down: silence reads as "nothing happened".
case "${NOTIFY_KIND:-slack}" in
  slack|email|both) ;;
  *) echo "WARNING: NOTIFY_KIND='${NOTIFY_KIND}' is not slack|email|both -"" falling back to slack. Notifications are NOT going where you configured them." >&2 ;;
esac
# AIQE_MOCK, resolved once. UNSET still means REAL — `make run-pr` depends on it
# and that default is not changing here. What changes is a value that is SET and
# unrecognized: `true`, `yes`, an empty string from a bare key in .env. Those all
# used to fall through the `= "1"` test to REAL adapters and real model spend,
# which is the wrong direction to guess in — somebody writing AIQE_MOCK=true is
# asking FOR mock, and was getting pushes to real repositories instead.
AIQE_MOCK_RESOLVED=0
case "$(printf '%s' "${AIQE_MOCK-0}" | tr 'A-Z' 'a-z')" in
  1|true|yes|on)  AIQE_MOCK_RESOLVED=1 ;;
  0|false|no|off) AIQE_MOCK_RESOLVED=0 ;;
  *) AIQE_MOCK_RESOLVED=1
     echo "WARNING: AIQE_MOCK='${AIQE_MOCK}' is not a recognized boolean"           "(1/true/yes/on or 0/false/no/off) - using MOCK adapters."           "Nothing will be pushed and no model will be billed." >&2 ;;
esac
if [ "$AIQE_MOCK_RESOLVED" = "1" ]; then
  SCM() { bash adapters/mock/scm.sh "$@"; }
  TRACKER() { bash adapters/mock/tracker.sh "$@"; }
  # Mock Slack by default; NOTIFY_KIND=email|both demos the email path (the email
  # adapter writes to out/mock-email/ under AIQE_MOCK=1).
  NOTIFY() {
    case "${NOTIFY_KIND:-slack}" in
      email) bash adapters/notify/email.sh "$@" ;;
      both)  bash adapters/mock/notify.sh "$@" || true; bash adapters/notify/email.sh "$@" ;;
      *)     bash adapters/mock/notify.sh "$@" ;;
    esac
  }
  TELEM() { bash adapters/mock/telemetry.sh "$@"; }
  if [ "${AIQE_REAL_LLM:-0}" = "1" ]; then
    # Parity mode: REAL claude -p phases against the demo estate + mock adapters
    _PHASE_IMPL() { bash engine/phases/run_phase.sh "$1" "prompts/$2" workspace "${@:3}"; }
  else
    _PHASE_IMPL() { bash engine/phases/mock_phase.sh "$1" "$KEY" workspace; }
  fi
else
  SCM() { bash "$(python3 -c "import yaml;print(yaml.safe_load(open('registry/org-config.yaml'))['adapters']['scm']['${SCM_KIND:-github}'])")" "$@"; }
  TRACKER() { bash adapters/tracker/jira.sh "$@"; }
  # Notify channel(s): NOTIFY_KIND=slack|email|both (default slack). Each channel is
  # best-effort so a down channel never aborts the run.
  NOTIFY() {
    case "${NOTIFY_KIND:-slack}" in
      email) bash adapters/notify/email.sh "$@" ;;
      both)  bash adapters/notify/slack.sh "$@" || true; bash adapters/notify/email.sh "$@" ;;
      *)     bash adapters/notify/slack.sh "$@" ;;
    esac
  }
  TELEM() { bash adapters/telemetry/splunk.sh "$@"; }
  _PHASE_IMPL() { bash engine/phases/run_phase.sh "$1" "prompts/$2" workspace "${@:3}"; }
fi

# JCTS-S3: every Tracker comment goes through one best-effort accounting
# boundary.  The helper posts through the same configured adapter, records a
# payload-free receipt + event, and always yields control back to the run.
TICKET_COMMENT() {
  local kind=$1 target=$2 body=$3
  local plan_args=()
  case "${MODE:-}" in
    plan|requirements) plan_args=(--plan-key "${KEY:-$target}") ;;
  esac
  python3 engine/lib/ticket_comment.py post "$kind" "$target" "$body" \
    "${plan_args[@]}" >/dev/null || true
}

# JCTS-S4: rendering is separate from delivery.  Each helper returns the exact
# legacy summary while the flag is off or rendering degrades, so rollout cannot
# change today's comment bodies accidentally.  Posting remains the S3 boundary.
TICKET_RICH_ENABLED() {
  python3 engine/lib/ticket_comment_render.py enabled >/dev/null 2>&1
}
TICKET_PLAN_COMMENT() {
  local target=$1 fallback=$2
  local body="$fallback" rendered=""
  if rendered=$(python3 engine/lib/ticket_comment_render.py plan \
      "$KEY" "$target" "$fallback") && [ -n "$rendered" ]; then
    body=$rendered
  fi
  TICKET_COMMENT plan "$target" "$body"
}
TICKET_DELIVERY_COMMENT() {
  local target=$1 fallback=$2 pr_ref=${3:-}
  local body="$fallback" rendered=""
  if rendered=$(python3 engine/lib/ticket_comment_render.py delivery \
      "$RUN_ID" "$KEY" "$target" "$fallback" "$pr_ref") \
      && [ -n "$rendered" ]; then
    body=$rendered
  fi
  TICKET_COMMENT delivery "$target" "$body"
}
TICKET_REFUSAL_COMMENT() {
  local kind=$1 target=$2 fallback=$3 reason=$4 fix=$5 pr_ref=${6:-}
  local body="$fallback" rendered=""
  if rendered=$(python3 engine/lib/ticket_comment_render.py refusal \
      "$RUN_ID" "$KEY" "$target" "$fallback" "$reason" "$fix" "$pr_ref") \
      && [ -n "$rendered" ]; then
    body=$rendered
  fi
  TICKET_COMMENT "$kind" "$target" "$body"
}

# Budget enforcement (docs/product-direction.md H1): every phase's actual spend —
# claude -p reports total_cost_usd in out/<phase>.json — lands in out/cost.tsv, and
# the guard runs BEFORE each phase. Over the cost or wall-clock limit the run aborts
# with exit 77, notifies, and never reaches the gate: a runaway loop can overshoot
# by at most one phase. Mock phases meter nothing (AIQE_MOCK_PHASE_COST simulates).
RUN_START=$(date +%s)
# Fresh run scratch: cost ledger, phase transcripts+contracts and gate results
# are all PER-RUN. Stale contracts get absorbed into this run's record
# (run_record.py globs out/*.contract.json); a stale out/<phase>.json is worse —
# a leftover real-run transcript carrying total_cost_usd poisons budget metering
# for every later mock run (phase_cost reads it as "metered", so the
# AIQE_MOCK_PHASE_COST simulation never applies and the exit-77 guard is dead).
rm -f "${AIQE_COST_LEDGER:-out/cost.tsv}" out/*.json out/gate_results.tsv \
      out/context-*.md out/context-retries.tsv out/cost-degrade.tsv \
      out/phase-skips.tsv out/phase-starts.jsonl out/comment-attempts.jsonl
# Run record write. NOT `... | tee file | TELEM`: tee creates and TRUNCATES
# the target before the producer emits a byte, so a producer that dies leaves
# a 0-byte or half-written record on disk - the durable evidence of a run that
# really happened. Both failure modes were reproduced (a malformed phase
# contract exiting run_record non-zero; a kill mid-stream past the pipe
# buffer), and the recovery story downstream is inconsistent: qa.py warns and
# names the file while the dashboard, the team report and the SCORECARD all
# swallow it silently - the scorecard's commit rate quietly measuring a
# different population.
#
# Produce to scratch, verify, then move into place. A failed record is
# REPORTED and leaves previous state untouched rather than replacing it with
# a lie.
write_run_record() {
  local rid="$1" mode="$2" key="$3" dest tmp
  dest="reports/runs/${rid}.json"
  tmp="out/.run-record-${rid}.json"
  if python3 engine/lib/run_record.py "$rid" "$mode" "$key" > "$tmp"; then
    if [ -s "$tmp" ] && python3 engine/lib/json_ok.py "$tmp"; then
      mv -f "$tmp" "$dest"
      TELEM emit_event < "$dest" || true
    else
      echo "[run-record] REFUSING to write $dest: producer output empty or not JSON" >&2
      rm -f "$tmp"
    fi
  else
    echo "[run-record] FAILED for $rid - no record written (state left intact)" >&2
    rm -f "$tmp"
  fi
}

_budget_guard() {
  local why
  if ! why=$(python3 engine/lib/budget.py check --start "$RUN_START"); then
    echo "$why"
    local msg="AI-QE run ${RUN_ID:-?} for ${KEY:-?} ABORTED before phase '$1': $why"
    case "${MODE:-}" in
      jira|plan|tests)
        TICKET_REFUSAL_COMMENT budget_abort "$KEY" "$msg" "$why" \
          "reduce the run scope or raise the configured workflow envelope, then retry"
        ;;
      pr)
        if [ "${PR_TICKET_FUSED:-0}" = "1" ] && [ -n "${PLAN_TICKET:-}" ] \
            && TICKET_RICH_ENABLED; then
          TICKET_REFUSAL_COMMENT budget_abort "$PLAN_TICKET" "$msg" "$why" \
            "reduce the run scope or raise the configured workflow envelope, then retry" \
            "$REPO#$PR"
        fi
        ;;
    esac
    NOTIFY post "$msg" || true
    EV run.aborted "${KEY:-?}" failed "phase=$1 reason=budget"
    exit 77
  fi
}
# PHASE captures the phase's own exit explicitly: if a caller ever attaches a
# `|| handler`, set -e is suppressed inside this body, so without the capture a
# failing _PHASE_IMPL would fall through and PHASE would return the metering
# line's 0 — making the caller's failure handler dead code.
# AIQE_PHASE_LABEL renames the phase's OUTPUT artifacts (and therefore its cost-ledger
# row) without changing which org-config policy it runs under — that is what lets
# generation fan out to one labeled call per test repo.
_ARCHIVE_INPUTS() {
  if [ "${AIQE_ARTIFACT_STORE:-0}" = "1" ]; then
    python3 engine/lib/task_bundle.py capture-phase "$@" >/dev/null ||
      echo "[artifact-bundle] capture unavailable for $3 (run continues)"
  fi
}

PHASE() {
  local label="${AIQE_PHASE_LABEL:-$1}"
  _budget_guard "$label"
  local rc=0
  _ARCHIVE_INPUTS "$RUN_ID" "$KEY" "$label" initial "prompts/$2" "${@:3}"
  _PHASE_IMPL "$@" || rc=$?
  python3 engine/lib/budget.py record "$label" "out/$label.json" "$rc" || true
  # Context-retry escape hatch (cost-reduction 2.3): a phase that ran on a
  # SCOPED context and reported `missing_context` gets ONE re-run with the full
  # estate — the miss is recorded so the scoping policy can be tuned instead of
  # silently degrading output.
  if [ "$rc" -eq 0 ] && [ "${AIQE_CONTEXT_RETRY:-1}" != "0" ]; then
    if python3 -c "import json,sys; c=json.load(open('out/${label}.contract.json')); sys.exit(0 if c.get('missing_context') else 1)" 2>/dev/null; then
      local args=() f swapped=0
      for f in "$@"; do
        case "$f" in out/context-*.md) args+=("$AIQE_P_AGENTS"); swapped=1 ;;
                     *) args+=("$f") ;; esac
      done
      if [ "$swapped" = "1" ]; then
        echo "[context] $label reported missing context — one retry with the full estate"
        python3 -c "import json; c=json.load(open('out/${label}.contract.json')); print('${label}\t' + '; '.join(map(str, c.get('missing_context') or [])))" >> out/context-retries.tsv 2>/dev/null || true
        _ARCHIVE_INPUTS "$RUN_ID" "$KEY" "$label" retry \
          "prompts/${args[1]}" "${args[@]:2}"
        _PHASE_IMPL "${args[@]}" || rc=$?
        python3 engine/lib/budget.py record "$label" "out/$label.json" "$rc" || true
      fi
    fi
  fi
  return $rc
}

# No-op phase skipping (cost-reduction 5.1): a phase that cannot change the
# outcome is a call never made — free savings. Each skip is logged to
# out/phase-skips.tsv so the run record and surfaces render "skipped (nothing
# to do)", distinct from a failure.
SKIP_PHASE() {  # $1 = phase, $2 = reason
  echo "[skip] $1: $2"
  printf '%s\t%s\n' "$1" "$2" >> out/phase-skips.tsv
}

# Retrieval-scoped context (cost-reduction 2.2): echo the per-run scoped file
# for a phase, or AGENTS.md when scoping is off (globally, per-phase in
# org-config `context_scope:`), unavailable, or failed. Fallback is ALWAYS the
# full estate — a scoping failure must never break or quietly starve a run.
CTX() {
  local phase="$1"
  local context
  if python3 engine/lib/context_scope.py assemble "$phase" >/dev/null 2>&1 \
     && [ -s "out/context-${phase}.md" ]; then
    context="out/context-${phase}.md"
  else
    context="$AIQE_P_AGENTS"
  fi
  # A2 keeps the estate context first but needs its budget manifest before it
  # can render optional ticket prose. CTX is already the single boundary that
  # resolves scoped-vs-full context; create the separate run-tail sidecar here
  # and let the caller append it last. With no selected ticket this is exactly
  # the historical CTX path and performs no extra work.
  if [ "${PR_TICKET_FUSED:-0}" = "1" ]; then
    case "$phase" in
      triage|generate)
        python3 engine/lib/ticket_context.py render out/ticket.json \
          out/ticket-discovery.json "$phase" "$context" \
          "out/pr-ticket-fused-${phase}.md" \
          "out/pr-ticket-fused-${phase}.json" || return 1 ;;
    esac
  fi
  echo "$context"
}

# Per-repo generation fan-out (openhands-review §3.3, reopened by the existing-approach
# feature). Generation used to be ONE call no matter how many test repos resolved, with
# every repo's conventions concatenated into a single out/repo-conventions.md — so on
# the case this platform exists for, a contract change fanning out to an API repo plus
# two consumer UI repos, one agent had to hold three repos' approaches at once and not
# cross-wire them. That is precisely the failure the existing-approach work set out to
# prevent, and this was the last place still inviting it.
#
# Now each resolved repo gets its own agent, its own conventions file, and its own
# labeled contract; merge_contracts.py restores the single pre-fan-out shape for
# validate and everything downstream. One repo resolved => the old single call, so the
# common case pays nothing. A per-repo failure is contained: the merge records the
# skipped repo and the other repos' tests still reach the gate, matching the partial
# success the per-repo gate already allows (§5.8.5).
GENERATE() {
  local repos n
  repos=$(python3 -c "import json;print(' '.join(json.load(open('out/resolve.contract.json'))['test_repos']))" 2>/dev/null || echo "")
  n=$(echo "$repos" | wc -w | tr -d ' ')
  if [ "${AIQE_GENERATE_FANOUT:-1}" = "0" ] || [ "$n" -lt 2 ]; then
    PHASE generate pr-generate.md "$@"
    return $?
  fi
  echo "[generate] fanning out to $n test repos: $repos"
  local repo conv rc any=0 ctx f
  for repo in $repos; do
    conv="out/repo-conventions-${repo}.md"
    # Only THIS repo's helpers and exemplars — the whole point of the fan-out.
    # Already written by the `all` pass during context prep, which profiles each
    # repo ONCE; rebuilding here re-scanned repos that pass had just scanned
    # (measured: 4.16s across three processes on a two-repo run, two of the
    # repo scans duplicates). Rebuild only if it is genuinely missing, so a
    # caller that skipped context prep still works.
    if [ ! -s "$conv" ]; then
      python3 engine/lib/spec_exemplars.py "$conv" "$repo" > /dev/null 2>&1 || : > "$conv"
    fi
    [ -f "$conv" ] || : > "$conv"
    # This repo's own existing-test rows, plus anything covering the app repos
    # this run touched — so the agent extends its own suite instead of
    # duplicating, and knows what is already covered elsewhere. Same reason the
    # conventions are per-repo: an agent writing into ONE repo should not be
    # reasoning over every other repo's catalog.
    slice="out/catalog-slice-${repo}.jsonl"
    # stderr is NOT discarded here: catalog_slice reports what it could not
    # read (an unreadable catalog file, a malformed row), and that report is
    # the only signal that the existing-test context handed to this agent is
    # short. Silencing it turned an honest warning into exactly the silent
    # duplicate-test failure the slice exists to prevent.
    python3 engine/lib/catalog_slice.py out/resolve.contract.json "$repo" \
      > "$slice" || cp out/catalog-slice.jsonl "$slice" 2>/dev/null || : > "$slice"
    # Swap the all-repos conventions file for this repo's. Done by rebuilding the
    # array rather than with ${@/../..}: the pattern contains slashes, which that
    # expansion treats as delimiters.
    ctx=()
    for f in "$@"; do
      if [ "$f" = "out/repo-conventions.md" ]; then ctx+=("$conv")
      elif [ "$f" = "out/catalog-slice.jsonl" ]; then ctx+=("$slice")
      else ctx+=("$f"); fi
    done
    rc=0
    # Set + unset explicitly rather than as an `VAR=x func` prefix: for a FUNCTION,
    # POSIX (and bash in posix mode) keeps such assignments after the call, which
    # would leak this repo's label onto validate and every later phase.
    export AIQE_PHASE_LABEL="generate-${repo}" AIQE_TARGET_REPO="$repo"
    PHASE generate pr-generate.md "${ctx[@]}" || rc=$?
    unset AIQE_PHASE_LABEL AIQE_TARGET_REPO
    if [ "$rc" -ne 0 ]; then
      echo "[generate] repo $repo failed (exit $rc) — other repos continue"
      rm -f "out/generate-${repo}.contract.json"
    else
      any=1
    fi
  done
  # shellcheck disable=SC2086  # word splitting is the intent — one arg per repo
  python3 engine/lib/merge_contracts.py generate out $repos
  # Every repo failing is a real generate failure — don't hand validate an empty
  # contract and call the run a success.
  [ "$any" = "1" ] || return 1
  return 0
}

# PRD v2 B1: one read-only reviewer call per resolved test repository after
# validation and before any gate. The reviewer is advisory in B1: every failure
# is converted to explicit unavailable evidence, and this function always
# returns zero. B2/B3 own repair and enforcement policy; neither is hidden here.
REVIEW_TESTS() {
  rm -f out/reviewer.contract.json out/reviewer-status.tsv
  if ! python3 engine/lib/test_reviewer.py enabled; then
    SKIP_PHASE review "AIQE_TEST_REVIEWER is disabled"
    return 0
  fi
  local repos repo input conv slice rc label iteration
  local ctx=()
  repos=$(python3 -c "import json;print(' '.join(json.load(open('out/resolve.contract.json'))['test_repos']))" 2>/dev/null || echo "")
  iteration=${AIQE_REVIEW_ITERATION:-0}
  : > out/reviewer-status.tsv
  for repo in $repos; do
    # Unique labels keep every re-review's contract and spend attributable.
    # A short-lived canonical copy exists only because merge() reads that name.
    rm -f "out/reviewer-${repo}.contract.json"
    label="reviewer-${iteration}-${repo}"
    input="out/${label}.input.json"
    rc=0
    python3 engine/lib/test_reviewer.py prepare "$repo" "$input" || rc=$?
    if [ "$rc" -eq 3 ]; then
      SKIP_PHASE "reviewer-$repo" "no generated tests to review"
      printf '%s\tskipped\t%s\n' "$repo" "no generated tests to review" >> out/reviewer-status.tsv
      continue
    elif [ "$rc" -ne 0 ]; then
      printf '%s\tunavailable\t%s\n' "$repo" "review input unavailable" >> out/reviewer-status.tsv
      continue
    fi

    conv="out/repo-conventions-${repo}.md"
    if [ ! -s "$conv" ]; then
      python3 engine/lib/spec_exemplars.py "$conv" "$repo" >/dev/null 2>&1 || : > "$conv"
    fi
    [ -f "$conv" ] || : > "$conv"
    slice="out/catalog-slice-${repo}.jsonl"
    if [ ! -f "$slice" ]; then
      python3 engine/lib/catalog_slice.py out/resolve.contract.json "$repo" \
        > "$slice" 2>/dev/null || cp out/catalog-slice.jsonl "$slice" 2>/dev/null || : > "$slice"
    fi
    ctx=("$input" out/validate.contract.json "$conv" "$slice")
    if [ -f out/testplan.contract.json ]; then ctx+=(out/testplan.contract.json)
    elif [ -f out/triage.contract.json ]; then ctx+=(out/triage.contract.json); fi
    for extra in out/ticket.json out/pr.diff out/changed.txt; do
      [ -f "$extra" ] && ctx+=("$extra")
    done

    export AIQE_PHASE_LABEL="$label" AIQE_TARGET_REPO="$repo"
    rc=0
    if [ "${AIQE_REVIEW_BUDGET_GUARD:-0}" = "1" ]; then _budget_guard "$label"; fi
    _ARCHIVE_INPUTS "$RUN_ID" "$KEY" "$label" initial prompts/test-reviewer.md "${ctx[@]}"
    _PHASE_IMPL reviewer test-reviewer.md "${ctx[@]}" || rc=$?
    python3 engine/lib/budget.py record "$label" "out/${label}.json" "$rc" || true
    unset AIQE_PHASE_LABEL AIQE_TARGET_REPO
    if [ "$rc" -eq 0 ] && python3 engine/lib/test_reviewer.py validate \
        "$repo" "out/${label}.contract.json"; then
      cp "out/${label}.contract.json" "out/reviewer-${repo}.contract.json"
      printf '%s\treviewed\t\n' "$repo" >> out/reviewer-status.tsv
    else
      echo "[reviewer] $repo unavailable — generated tests continue to gate"
      rm -f "out/${label}.contract.json"
      printf '%s\tunavailable\t%s\n' "$repo" "reviewer failed or returned malformed output" >> out/reviewer-status.tsv
    fi
  done
  python3 engine/lib/test_reviewer.py merge out/reviewer-status.tsv \
    out/reviewer.contract.json || {
      printf '%s\n' '{"artifact":"test-reviewer","schema":1,"state":"unavailable","verdict":"unavailable","repos":[{"repo":"reviewer","state":"unavailable","reason":"review result merge failed"}],"findings":[],"simulated":false}' \
        > out/reviewer.contract.json
    }
  for repo in $repos; do rm -f "out/reviewer-${repo}.contract.json"; done
  return 0
}

# PRD v2 B2: a needs-work verdict gets at most review.max_loops repair passes.
# Each affected repo has its own write-enabled repair call, then one validation
# and one read-only reviewer fan-out. Unlike the initial advisory review, every
# call in this loop is budget-guarded: B2 is optional extra spend after a finding.
REPAIR_FROM_REVIEW() {
  local max iteration repos repo label input conv slice validate_label
  local repair_paths=() ctx=()
  max=$(python3 engine/lib/review_repair.py max-loops) || return 1
  [ "$max" -gt 0 ] || return 0
  if ! python3 engine/lib/review_repair.py start \
      out/reviewer.contract.json out/review-history.json 2>/dev/null; then
    return 0
  fi
  for iteration in $(seq 1 "$max"); do
    repos=$(python3 engine/lib/review_repair.py pending out/reviewer.contract.json) || return 1
    [ -n "$repos" ] || break
    echo "[review-repair] iteration $iteration/$max for: $repos"
    repair_paths=()
    for repo in $repos; do
      label="reviewrepair-${iteration}-${repo}"
      input="out/${label}.input.json"
      python3 engine/lib/review_repair.py prepare "$repo" "$iteration" \
        out/reviewer.contract.json "$input" "$AIQE_ROOT"
      conv="out/repo-conventions-${repo}.md"
      slice="out/catalog-slice-${repo}.jsonl"
      [ -f "$conv" ] || : > "$conv"
      [ -f "$slice" ] || : > "$slice"
      ctx=("$input" out/generate.contract.json out/validate.contract.json "$conv" "$slice")
      for extra in out/testplan.contract.json out/triage.contract.json \
          out/ticket.json out/pr.diff out/changed.txt; do
        [ -f "$extra" ] && ctx+=("$extra")
      done
      export AIQE_PHASE_LABEL="$label" AIQE_TARGET_REPO="$repo" \
        AIQE_REPAIR_INPUT="$input" AIQE_REVIEW_ITERATION="$iteration"
      PHASE reviewrepair review-repair.md "${ctx[@]}"
      unset AIQE_PHASE_LABEL AIQE_TARGET_REPO AIQE_REPAIR_INPUT AIQE_REVIEW_ITERATION
      python3 engine/lib/review_repair.py validate "$repo" "$iteration" \
        out/reviewer.contract.json "$input" "out/${label}.contract.json" "$AIQE_ROOT"
      python3 engine/lib/review_repair.py apply "$repo" "$iteration" \
        out/reviewer.contract.json "$input" "out/${label}.contract.json" \
        out/generate.contract.json "$AIQE_ROOT"
      repair_paths+=("out/${label}.contract.json")
    done

    validate_label="validate-review-${iteration}"
    export AIQE_PHASE_LABEL="$validate_label"
    PHASE validate validate-repair.md out/generate.contract.json out/repo-conventions.md
    unset AIQE_PHASE_LABEL
    cp "out/${validate_label}.contract.json" out/validate.contract.json

    export AIQE_REVIEW_ITERATION="$iteration" AIQE_REVIEW_BUDGET_GUARD=1
    REVIEW_TESTS
    unset AIQE_REVIEW_ITERATION AIQE_REVIEW_BUDGET_GUARD
    python3 engine/lib/review_repair.py record "$iteration" \
      out/review-history.json out/validate.contract.json \
      out/reviewer.contract.json "${repair_paths[@]}"
  done
}

RUN_ID=$(date +%s)-$RANDOM
# Observability slice 1 (story 1.2). Runs previously emitted telemetry ONCE, at
# the very end, so an abort, a gate refusal or a mid-phase death produced
# nothing — the stream systematically under-reported failure, which is the
# opposite of what it is for. EV records every exit path. `|| true` on top of
# event_log's own never-raise contract: two guards, because logging must never
# change a run's exit code.
EV() {
  python3 engine/lib/event_log.py --emit "$@" >/dev/null 2>&1 || true
}
export RUN_ID
PR_TICKET_CONTEXT=()
PR_TRIAGE_FUSION_CONTEXT=()
PR_GENERATE_FUSION_CONTEXT=()
PR_TICKET_FUSED=0
DISCOVERED_TICKET=""
PLAN_TICKET=""
PR_PLAN=0
STORED_PLAN_TICKET=""
if [ "$MODE" = "plan" ] && [ "$#" -ge 3 ]; then
  case "$(printf '%s' "${AIQE_PR_PLAN:-0}" | tr 'A-Z' 'a-z')" in
    1|true|yes|on) PR_PLAN=1 ;;
    0|false|no|off) echo "PR_PLAN_DISABLED: set AIQE_PR_PLAN=1 to enable plan-first from PR"; exit 64 ;;
    *) echo "PR_PLAN_DISABLED: AIQE_PR_PLAN is not a recognized boolean; using OFF"; exit 64 ;;
  esac
  REPO=$2; PR=$3; export KEY="PR-${REPO}-${PR}"
  [[ "$PR" =~ ^[1-9][0-9]{0,8}$ ]] || {
    echo "INVALID_PR: $PR (expected 1-9 digits starting at 1)"; exit 64; }
elif [ "$MODE" = "tests" ]; then
  export KEY=$2
  case "$KEY" in *[!A-Za-z0-9._-]*|"") echo "INVALID_KEY: $KEY"; exit 64;; esac
  # The existing approval gate remains the single resume authority for both
  # ticket and PR plans. Target metadata only tells this run where to fetch and
  # report; it does not create another state machine.
  python3 engine/lib/sdd_messages.py require-approved "$KEY"
  PLAN_TARGET_KIND=$(python3 -c "import sys;sys.path.insert(0,'engine/lib');import plan_state;print((plan_state.get('$KEY').get('target') or {}).get('kind',''))")
  if [ "$PLAN_TARGET_KIND" = "pr" ]; then
    case "$(printf '%s' "${AIQE_PR_PLAN:-0}" | tr 'A-Z' 'a-z')" in
      1|true|yes|on) PR_PLAN=1 ;;
      *) echo "PR_PLAN_DISABLED: set AIQE_PR_PLAN=1 to resume this PR plan"; exit 64 ;;
    esac
    REPO=$(python3 -c "import sys;sys.path.insert(0,'engine/lib');import plan_state;print(plan_state.get('$KEY')['target']['repo'])")
    PR=$(python3 -c "import sys;sys.path.insert(0,'engine/lib');import plan_state;print(plan_state.get('$KEY')['target']['pr'])")
    STORED_PLAN_TICKET=$(python3 -c "import sys;sys.path.insert(0,'engine/lib');import plan_state;print(plan_state.get('$KEY')['target'].get('ticket',''))")
    [ "$KEY" = "PR-${REPO}-${PR}" ] || { echo "INVALID_PR_PLAN_TARGET: stored target does not match $KEY"; exit 64; }
  fi
fi
if [ "$MODE" = "pr" ] || [ "$PR_PLAN" = "1" ]; then
  if [ "$MODE" = "pr" ]; then REPO=$2; PR=$3; export KEY="PR-${REPO}-${PR}"; fi
  case "$KEY" in *[!A-Za-z0-9._-]*) echo "INVALID_KEY: $KEY"; exit 64;; esac
  if [ "$PR_PLAN" = "1" ]; then
    python3 engine/lib/plan_state.py require-requirements "$KEY" --pr
  fi
  # Shared scratch survives process crashes and later runs. Clear every fixed
  # A1/A2 artifact before consulting the flag so an OFF/no-selection retry can
  # never inherit a prior run's ticket, guidance, provenance, or prompt tail.
  rm -f out/pr-context.json out/ticket-discovery.json out/ticket-validation.tsv \
    out/pr-ticket-context.md out/discovered-ticket.json out/ticket.json \
    out/issue-guidance.md out/pr-ticket-fused-triage.md \
    out/pr-ticket-fused-generate.md out/pr-ticket-fused-triage.json \
    out/pr-ticket-fused-generate.json
  # Successor PRD A1: opt-in, deterministic ticket discovery. SCM supplies PR
  # metadata; Tracker validates every candidate. With the flag off this creates
  # no file, makes no extra port call, and adds no phase argument.
  PR_TICKET_ENABLED=0
  case "$(printf '%s' "${AIQE_PR_TICKET_CONTEXT:-0}" | tr 'A-Z' 'a-z')" in
    1|true|yes|on) PR_TICKET_ENABLED=1 ;;
    0|false|no|off) ;;
    *) echo "[config] AIQE_PR_TICKET_CONTEXT is not a recognized boolean — using OFF" >&2 ;;
  esac
  # Resume keeps delivery symmetry even if the operator did not repeat the
  # discovery flag: the validated ticket captured with the approved plan is
  # revalidated through the same A1/A2 path.
  if [ -n "$STORED_PLAN_TICKET" ]; then
    PR_TICKET_ENABLED=1
    export AIQE_PR_TICKET="$STORED_PLAN_TICKET"
  fi
  if [ "$PR_TICKET_ENABLED" = "1" ]; then
    if ! SCM pr_context "$REPO" "$PR" > out/pr-context.json 2>/dev/null; then
      printf '%s\n' '{"state":"unavailable","reason":"SCM PR metadata unavailable"}' \
        > out/pr-context.json
    fi
    python3 engine/lib/ticket_discovery.py extract out/pr-context.json \
      "${AIQE_PR_TICKET:-}" > out/ticket-discovery.json
    : > out/ticket-validation.tsv
    while IFS= read -r candidate; do
      candidate=${candidate%$'\r'}   # native Windows Python writes CRLF under Git Bash
      [ -n "$candidate" ] || continue
      candidate_file="out/discovered-ticket-${candidate}.json"
      if TRACKER get_item "$candidate" > "$candidate_file" 2>/dev/null; then
        if response_reason=$(python3 engine/lib/ticket_discovery.py \
          validate-response "$candidate" "$candidate_file"); then
          response_reason=$(printf '%s' "$response_reason" | tr -d '\r')
          printf '%s\tvalid\t%s\n' "$candidate" "$response_reason" \
            >> out/ticket-validation.tsv
        else
          response_reason=$(printf '%s' "$response_reason" | tr '\t\r\n' '   ')
          printf '%s\tunavailable\t%s\n' "$candidate" "$response_reason" \
            >> out/ticket-validation.tsv
        fi
      else
        vrc=$?
        if [ "$vrc" = "3" ]; then
          printf '%s\tinvalid\ttracker item not found\n' "$candidate" >> out/ticket-validation.tsv
        else
          printf '%s\tunavailable\ttracker validation unavailable (exit %s)\n' \
            "$candidate" "$vrc" >> out/ticket-validation.tsv
        fi
      fi
    done < <(python3 engine/lib/ticket_discovery.py keys out/ticket-discovery.json)
    python3 engine/lib/ticket_discovery.py resolve out/ticket-discovery.json \
      out/ticket-validation.tsv > out/.ticket-discovery.tmp
    mv out/.ticket-discovery.tmp out/ticket-discovery.json
    DISCOVERED_TICKET=$(python3 engine/lib/ticket_discovery.py selected \
      out/ticket-discovery.json | tr -d '\r')
    if [ -n "$DISCOVERED_TICKET" ]; then
      # The validated bytes become the ONE canonical ticket document consumed
      # by both workflows. No second Tracker call and no parallel A2 schema.
      cp "out/discovered-ticket-${DISCOVERED_TICKET}.json" out/.ticket.json.tmp
      mv out/.ticket.json.tmp out/ticket.json
      cp out/ticket.json out/discovered-ticket.json   # A1 compatibility alias
      # A1.6 records status on the discovery provenance from the SAME validated
      # response. Annotation never refetches and never changes selection.
      python3 engine/lib/ticket_discovery.py annotate out/ticket-discovery.json \
        out/ticket.json > out/.ticket-discovery.tmp
      mv out/.ticket-discovery.tmp out/ticket-discovery.json
      eval "$(python3 engine/lib/ticket_fields.py out/ticket.json)"
      cp "prompts/issue-types/${AIQE_T_GUIDANCE}.md" out/issue-guidance.md
      PR_TICKET_FUSED=1
      PR_TRIAGE_FUSION_CONTEXT=(out/issue-guidance.md out/pr-ticket-fused-triage.md)
      PR_GENERATE_FUSION_CONTEXT=(out/issue-guidance.md out/pr-ticket-fused-generate.md)
    fi
    python3 engine/lib/ticket_discovery.py context out/ticket-discovery.json \
      > out/pr-ticket-context.md
    PR_TICKET_CONTEXT=(out/pr-ticket-context.md)
    DISCOVERY_OUTCOME=$(python3 -c "import json;print(json.load(open('out/ticket-discovery.json'))['outcome'])" | tr -d '\r')
    if [ "$DISCOVERY_OUTCOME" = "ambiguous" ]; then
      DISCOVERY_KEYS=$(python3 -c "import json;d=json.load(open('out/ticket-discovery.json'));print(', '.join(d.get('validated_keys') or []))" | tr -d '\r')
      SCM comment "$REPO" "$PR" "AI-QE ticket discovery was ambiguous (${DISCOVERY_KEYS}). Generation continues without ticket context; requeue with an explicit ticket key." || true
    fi
  fi
  PLAN_TICKET=${DISCOVERED_TICKET:-$STORED_PLAN_TICKET}
  SCM changed_files "$REPO" "$PR" > out/changed.txt
  # P0: the actual patch, not just the file list — triage reviews real hunks
  SCM diff "$REPO" "$PR" > out/pr.diff 2>/dev/null || : > out/pr.diff
  python3 engine/phases/resolve.py pr "$REPO" --changed-files out/changed.txt > out/resolve.contract.json
  if [ "$PR_PLAN" = "1" ]; then
    [ -f out/issue-guidance.md ] || cp prompts/issue-types/Story.md out/issue-guidance.md
    : > out/confluence.md
    python3 -c 'import json,sys;json.dump({"kind":"pr","repo":sys.argv[1],"pr":sys.argv[2],"ticket":sys.argv[3]},open("out/plan-target.json","w",encoding="utf-8"),indent=2)' \
      "$REPO" "$PR" "$PLAN_TICKET"
  fi
else
  export KEY=${KEY:-$2}
  case "$KEY" in *[!A-Za-z0-9._-]*|"") echo "INVALID_KEY: $KEY"; exit 64;; esac
  # Generation from a plan is gated on human approval — check BEFORE any clone/LLM work
  if [ "$MODE" = "tests" ]; then
    python3 engine/lib/sdd_messages.py require-approved "$KEY"
  fi
  # SDD 2.2: when the requirements gate is ON, planning requires validated
  # requirements — checked BEFORE any clone/LLM work, like the plan gate.
  if [ "$MODE" = "plan" ] || [ "$MODE" = "jira" ]; then
    python3 engine/lib/sdd_messages.py require-requirements "$KEY"
  fi
  # P0: inline JIRA context ("pass JIRA context as text input") bypasses the tracker
  if [ -n "${AIQE_INLINE_FILE:-}" ]; then
    cp "$AIQE_INLINE_FILE" out/ticket.json
  else
    TRACKER get_item "$KEY" > out/ticket.json
  fi
  # ONE parse of the ticket for all five fields. These were five separate
  # `python3 -c` one-liners, each a ~200ms interpreter start reading the SAME
  # file — the spec_exemplars duplicated-work shape again. Values are
  # shlex-quoted by the emitter because ticket text is untrusted JIRA data and
  # this line evals them (same precedent as app_paths --sh).
  eval "$(python3 engine/lib/ticket_fields.py out/ticket.json)"
  COMP=$AIQE_T_COMP
  LBL=$AIQE_T_LBL
  LINKED=$AIQE_T_LINKED
  python3 engine/phases/resolve.py jira "$KEY" --components "$COMP" --labels "$LBL" --linked-repos "$LINKED" > out/resolve.contract.json
  # Release tracking: capture the ticket's fixVersions as the key's target release
  FIXV=$AIQE_T_FIXV
  if [ -n "$FIXV" ]; then python3 engine/lib/review_state.py release "$KEY" "$FIXV" jira; fi
  # Knowledge port: pull linked Confluence pages (budgeted) as analyze context
  # $AIQE_MOCK_RESOLVED, not the raw variable. Reading it again here meant
  # AIQE_MOCK=true selected mock adapters everywhere ELSE and a REAL Confluence
  # fetch right here — one run, two adapter modes, and an external call in what
  # the operator believed was a dry run.
  if [ "$AIQE_MOCK_RESOLVED" = "1" ]; then echo "## Linked PRD (mock): discounts must be 1-90%" > out/confluence.md; \
  else bash adapters/knowledge/confluence.sh get_linked_docs out/ticket.json > out/confluence.md || true; fi
  # One parse and one guidance policy for JIRA and fused-PR paths. Security
  # label/type precedence lives in ticket_fields.py and is unit-pinned.
  cp "prompts/issue-types/${AIQE_T_GUIDANCE}.md" out/issue-guidance.md
fi

# Who owns this checkout right now. The lock is an empty directory released with
# `rmdir`, so the identity cannot live inside it without breaking release — it
# goes beside it. This is what lets the UI attribute LIVE progress to the
# request a user submitted; without it a run started from the CLI (make demo-pr)
# is visible as "something is running" but not as "your ticket is on step 3".
# Left in place after the run: combined with the lock's absence it distinguishes
# "finished" from "still going", and the next run overwrites it.
printf '{"run_id":"%s","mode":"%s","key":"%s","started_ts":%s}\n' \
  "$RUN_ID" "$MODE" "$KEY" "$(date +%s)" > out/run-context.json 2>/dev/null || true

if [ "$(python3 -c "import json;print(json.load(open('out/resolve.contract.json')).get('needs_clarification', False))")" = "True" ]; then
  MSG="AI-QE cannot confidently route ${KEY}. Candidates: $(cat out/resolve.contract.json). Reply with '@openhands use <repos>'."
  case "$MODE" in jira|plan|tests) TICKET_COMMENT routing_clarification "$KEY" "$MSG" ;; esac
  NOTIFY post "$MSG"
  exit 0
fi

# Honor the resolver's skip decision: a PR touching no testable paths (or resolving
# to no test repos) has nothing to sync — end here rather than spending LLM phases
# and posting a build status for work that never existed.
if [ "$(python3 -c "import json;d=json.load(open('out/resolve.contract.json'));print(bool(d.get('skip')) or ('$MODE'=='pr' and not d.get('test_repos')))")" = "True" ]; then
  echo "RESOLVE_SKIP: no testable changes / no test repos resolved for ${KEY} — nothing to do."
  exit 0
fi

# Multi-clone workspace: read-only sources, writable test repos (§5.8.3)
for r in $(python3 -c "import json;print(' '.join(json.load(open('out/resolve.contract.json'))['source_repos']))"); do
  python3 engine/lib/checkout_workspace.py prepare src "$r" >/dev/null
  SCM clone_ro "$r" "workspace/src/$r"
done
# Partial success starts HERE (§5.8.5): one test repo whose clone fails — bad
# credentials, renamed slug, a mapped repo with no material yet — must not kill
# the work every OTHER repo would get. Failed clones are skipped with a
# clone_failed gate row so the run record and summary stay honest.
: > out/cloned-tests.txt
: > out/clone_failures.tsv
for t in $(python3 -c "import json;print(' '.join(json.load(open('out/resolve.contract.json'))['test_repos']))"); do
  if ! python3 engine/lib/checkout_workspace.py prepare tests "$t" >/dev/null; then
    echo "[warn] unsafe or unavailable checkout destination for test repo '$t' — skipping it"
    printf '%s\tclone_failed\t1\t\n' "$t" >> out/clone_failures.tsv
    continue
  fi
  if SCM clone_rw "$t" "workspace/tests/$t" "test/${KEY}-ai-qe"; then
    echo "$t" >> out/cloned-tests.txt
  else
    echo "[warn] clone failed for test repo '$t' — skipping it this run"
    printf '%s\tclone_failed\t1\t\n' "$t" >> out/clone_failures.tsv
  fi
done

# Refresh estate knowledge from the just-cloned sources so every LLM phase sees
# CURRENT contracts/routes/coverage (AGENTS.md is passed as phase context below).
python3 bin/gen_agents_md.py > /dev/null || true

# Catalog slice: existing-test knowledge handed to the phases (P2), FILTERED by
# the same `covers:` mapping that routed this run. It used to be a plain
# concatenation of every catalog/*.jsonl — a PR resolving one API repo still
# received the UI repo's rows, which is token cost and dilution on a real
# estate, and contradicts the fan-out design where each agent sees only its own
# conventions. An empty selection falls back to the whole catalog and says so:
# starving generation of existing-test context makes it duplicate work it
# cannot see, which is worse than over-feeding it.
python3 engine/lib/catalog_slice.py out/resolve.contract.json \
  > out/catalog-slice.jsonl 2>/dev/null || {
    : > out/catalog-slice.jsonl
    for _cf in "$AIQE_P_CATALOG"/*.jsonl; do
      [ -f "$_cf" ] || continue
      [ "$(basename "$_cf")" = "catalog.sample.jsonl" ] && continue
      grep -h . "$_cf" >> out/catalog-slice.jsonl 2>/dev/null || true
    done
  }
# Coverage gaps: surface with NO test evidence — generation targets these first
python3 engine/lib/coverage_gaps.py md > out/coverage-gaps.md 2>/dev/null || : > out/coverage-gaps.md
# Existing-approach exemplars: REAL helper + spec code from each resolved test repo,
# so generated tests mirror the repo's own approach instead of inventing a new one.
# `all` writes the combined file AND one per repo in a single process. The
# fan-out needs the per-repo files and used to build each in its own
# interpreter, re-profiling repos this pass had already read.
python3 engine/lib/spec_exemplars.py all out/repo-conventions.md \
  $(python3 -c "import json;print(' '.join(json.load(open('out/resolve.contract.json'))['test_repos']))") \
  > /dev/null 2>&1 || : > out/repo-conventions.md
[ -f out/repo-conventions.md ] || : > out/repo-conventions.md

# A3 change-to-test impact analysis. The helper leaves no artifact while the
# preview flag is off, preserving the old generate context byte-for-byte. When
# enabled, failure is not converted into an empty/silent result: A3 requires an
# explicit candidate or explicit no-candidate artifact.
IMPACT_CONTEXT=()
RUN_IMPACT() {
  python3 engine/lib/impact_analysis.py "$1" "$KEY"
  IMPACT_CONTEXT=()
  if [ -s out/impact-candidates.json ]; then
    IMPACT_CONTEXT=(out/impact-candidates.json)
  fi
}

# A4 is advisory by contract. Even with its preview flag enabled, a detector
# failure may not block generation or the gate; the warning is simply
# unavailable for this run and the error remains visible in pipeline output.
RUN_DUPLICATES() {
  if ! python3 engine/lib/duplicate_detector.py "$1" "$KEY"; then
    echo "[duplicate-detection] advisory detector failed — generation continues"
    rm -f out/duplicate-warnings.json
  fi
}

# Control-repo artifacts (test plans, canonical data) belong at the root; real phases
# run with cwd=workspace so relocate anything written there (no-op in mock mode).
relocate_artifacts() {
  for d in testplans testdata; do
    if [ -d "workspace/$d" ]; then mkdir -p "$d"; cp -r "workspace/$d/." "$d/"; rm -rf "workspace/$d"; fi
  done
}

# Phase chain (Workflow A: triage->generate->validate; B: analyze->plan->data->generate->validate)
if [ "$MODE" = "pr" ]; then
  PHASE triage pr-triage.md "$(CTX triage)" out/resolve.contract.json out/changed.txt out/pr.diff out/catalog-slice.jsonl out/coverage-gaps.md "${PR_TICKET_CONTEXT[@]}" "${PR_TRIAGE_FUSION_CONTEXT[@]}"
  # Extend-vs-create scout (roadmap 2.1): deterministic join of the diff's surface
  # against catalog evidence, emitting NAMED extend targets. Tolerant — a scout
  # failure yields an empty file, never a failed run.
  python3 engine/lib/extend_scout.py > out/extend-candidates.md 2>/dev/null || : > out/extend-candidates.md
  RUN_IMPACT pr
  GENERATE "$(CTX generate)" out/triage.contract.json out/pr.diff out/catalog-slice.jsonl out/extend-candidates.md "${IMPACT_CONTEXT[@]}" out/coverage-gaps.md out/repo-conventions.md "${PR_TICKET_CONTEXT[@]}" "${PR_GENERATE_FUSION_CONTEXT[@]}"
  # PR mode has no scenario artifact before generation. Compare the generated
  # proposal before validation/reporting; this warning never changes the files.
  RUN_DUPLICATES pr
elif [ "$MODE" = "tests" ]; then
  # Resume from the APPROVED plan. The reviewed markdown is authoritative (it may have
  # been edited), so it is passed to both phases alongside the snapshotted contract.
  # The snapshot must exist (plan mode wrote it) — a silent fallback here would let
  # a stale contract from a different key shape generation.
  if ! cp "reports/plans/${KEY}.contract.json" out/testplan.contract.json 2>/dev/null; then
    echo "PLAN_SNAPSHOT_MISSING: reports/plans/${KEY}.contract.json — re-run 'pipeline.sh plan ${KEY}'"
    exit 64
  fi
  RUN_IMPACT tests
  RUN_DUPLICATES tests
  if python3 -c "import json,sys; c=json.load(open('out/testplan.contract.json')); sys.exit(0 if c.get('data_needs')=='none' else 1)" 2>/dev/null; then
    SKIP_PHASE testdata "plan declares data_needs: none"
  else
    PHASE testdata jira-testdata.md "$(CTX testdata)" out/testplan.contract.json "$AIQE_P_TESTPLANS/${KEY}.md"
  fi
  GENERATE "$(CTX generate)" out/issue-guidance.md out/testplan.contract.json out/testdata.contract.json "$AIQE_P_TESTPLANS/${KEY}.md" out/catalog-slice.jsonl "${IMPACT_CONTEXT[@]}" out/repo-conventions.md
else
  if [ "$PR_PLAN" = "1" ]; then
    # A3 plan authoring is driven by the actual patch. The validated fused
    # ticket, when present, enriches intent but never replaces diff authority.
    PHASE analyze jira-analyze.md "$(CTX analyze)" out/issue-guidance.md \
      out/pr.diff out/changed.txt "${PR_TICKET_CONTEXT[@]}" \
      "${PR_GENERATE_FUSION_CONTEXT[@]}"
  else
    PHASE analyze jira-analyze.md "$(CTX analyze)" out/issue-guidance.md out/ticket.json out/confluence.md
  fi
  # SDD (story 2.1): persist the EARS requirements spec the analyze contract
  # carries — the trace matrix's requirement end, and what a human validates
  # when the requirements gate (2.2) is on. Best-effort; legacy contracts
  # (behaviors only) write nothing.
  python3 engine/lib/spec_store.py write-requirements "$KEY" out/analyze.contract.json >/dev/null 2>&1 || true
  # SDD 2.2: requirements mode stops HERE — the human validates WHAT before
  # the platform plans HOW. Blocking questions ride the ticket comment.
  if [ "$MODE" = "requirements" ]; then
    python3 engine/lib/plan_state.py requirements-record "$KEY" > /dev/null
    RQ_MSG="AI-QE formalized requirements for ${KEY} (specs/${KEY}/requirements.yaml) — validate and approve with: make requirements-approve KEY=${KEY}"
    if BLOCKING=$(python3 engine/lib/spec_store.py blocking "$KEY" 2>/dev/null); then
      RQ_MSG="${RQ_MSG}. OPEN QUESTIONS NEEDING ANSWERS: $(echo "$BLOCKING" | tr '
' ' ')"
    fi
    TICKET_COMMENT requirements "$KEY" "$RQ_MSG"
    NOTIFY post "$RQ_MSG" || true
    echo "REQUIREMENTS_STATUS=DRAFT specs/${KEY}/requirements.yaml"
    echo "$RQ_MSG"
    exit 0
  fi
  # SDD 2.3: a BLOCKING ambiguity (contradictory/undefined ACs) stops the
  # chain before planning — the platform asks instead of guessing, extending
  # the resolver's needs_clarification pattern. Non-blocking ambiguities flow
  # through and render in the plan editor.
  if BLOCKING=$(python3 engine/lib/spec_store.py blocking "$KEY" 2>/dev/null); then
    CL_MSG="AI-QE NEEDS CLARIFICATION for ${KEY} before planning: $(echo "$BLOCKING" | tr '
' ' ') — answer on the ticket, then re-run."
    if [ "$PR_PLAN" = "1" ]; then
      SCM comment "$REPO" "$PR" "$CL_MSG" || true
      if [ -n "$PLAN_TICKET" ]; then TICKET_COMMENT clarification "$PLAN_TICKET" "$CL_MSG"; fi
    else
      TICKET_COMMENT clarification "$KEY" "$CL_MSG"
    fi
    NOTIFY post "$CL_MSG" || true
    echo "NEEDS_CLARIFICATION: $(echo "$BLOCKING" | head -1)"
    exit 65
  fi
  # Semantic plan reuse (cost-reduction 3.3): PLAN MODE ONLY — reuse without the
  # human draft gate would skip exactly the review that makes it safe. A hit
  # replaces the testplan LLM phase with deterministic adaptation of a prior
  # APPROVED plan; the adversary below still challenges the adapted draft.
  # Default OFF (AIQE_PLAN_REUSE=1 enables) until the 7.2 quality eval.
  PLAN_REUSED=0
  if [ "$MODE" = "plan" ] && [ "${AIQE_PLAN_REUSE:-0}" = "1" ]; then
    if REUSE_LINE=$(python3 engine/lib/plan_reuse.py try "$KEY" 2>/dev/null); then
      PLAN_REUSED=1
      echo "[plan-reuse] $REUSE_LINE — testplan phase skipped"
    fi
  fi
  if [ "$PLAN_REUSED" != "1" ]; then
    rm -f out/plan-reuse.json     # a fresh authoring must not inherit stale provenance
    PHASE testplan jira-testplan.md "$(CTX testplan)" out/issue-guidance.md out/analyze.contract.json out/coverage-gaps.md
  fi
  # Adversarial plan review: the plan is the artifact a human approves, and until now
  # one agent wrote it with nothing arguing back. A read-only ADVERSARY hunts for what
  # the author missed (negative/boundary/authz/state/cross-repo gaps) and an ARBITER
  # judges each finding and folds the accepted ones in — only ever ADDING scenarios.
  # It runs before the human gate, so it changes what the reviewer is asked to approve,
  # never whether they are asked. Failure of either phase is non-fatal by design: the
  # authored plan simply stands, exactly as it did before this existed.
  ADVERSARY_LINE=""
  rm -f out/planadversary.contract.json out/planarbiter.contract.json
  # Skip when the plan has no scenarios (5.1): an adversary of an empty plan
  # has nothing to challenge — the human gate will reject it anyway.
  if python3 -c "import json,sys; c=json.load(open('out/testplan.contract.json')); sys.exit(1 if c.get('scenarios') else 0)" 2>/dev/null; then
    SKIP_PHASE planadversary "zero-scenario plan — nothing to challenge"
  elif python3 engine/lib/plan_adversary.py enabled; then
    ADV_RC=0
    PHASE planadversary jira-plan-adversary.md "$(CTX planadversary)" out/analyze.contract.json \
      out/testplan.contract.json "$AIQE_P_TESTPLANS/${KEY}.md" out/coverage-gaps.md || ADV_RC=$?
    if [ "$ADV_RC" -ne 0 ]; then
      echo "[plan-adversary] phase failed — the authored plan stands"
      rm -f out/planadversary.contract.json
    elif [ -f out/planadversary.contract.json ]; then
      ARB_RC=0
      PHASE planarbiter jira-plan-arbitrate.md "$(CTX planarbiter)" out/analyze.contract.json \
        out/testplan.contract.json out/planadversary.contract.json \
        "$AIQE_P_TESTPLANS/${KEY}.md" out/resolve.contract.json || ARB_RC=$?
      # The arbiter's contract REPLACES the plan contract only on success — a failed
      # arbitration must not hand testdata/generate a half-written scenario set.
      if [ "$ARB_RC" -eq 0 ] && [ -s out/planarbiter.contract.json ]; then
        # SDD (story 1.1): merge, don't copy — a re-emitting arbiter must not
        # silently strip the author's steps/verification from matching
        # scenarios. Fallback to the plain copy on any merge failure.
        python3 engine/lib/spec_store.py merge-fold out/testplan.contract.json out/planarbiter.contract.json 2>/dev/null \
          || cp out/planarbiter.contract.json out/testplan.contract.json
      else
        echo "[plan-adversary] arbitration failed — the authored plan stands"
        rm -f out/planarbiter.contract.json
      fi
    fi
    ADVERSARY_LINE=$(python3 engine/lib/plan_adversary.py summary || echo "")
    # `|| true`: an empty line is the normal "disabled/no signal" case, and a bare
    # `[ -n .. ] && echo` returning 1 as the last statement here would trip set -e.
    if [ -n "$ADVERSARY_LINE" ]; then echo "[plan-adversary] $ADVERSARY_LINE"; fi
  fi
  # The JIRA query includes the ticket acceptance criteria plus the FINAL
  # authored/arbitrated scenario set, so it must run after plan arbitration.
  RUN_DUPLICATES "$MODE"
  RUN_IMPACT "$MODE"
  if [ "$MODE" = "plan" ]; then
    # STOP: the plan awaits human review/edit/approval. No test code, no commit.
    relocate_artifacts
    if [ "$PR_PLAN" = "1" ]; then
      python3 engine/lib/plan_state.py record "$KEY" out/testplan.contract.json \
        "$ADVERSARY_LINE" out/plan-target.json > /dev/null
    else
      python3 engine/lib/plan_state.py record "$KEY" out/testplan.contract.json "$ADVERSARY_LINE" > /dev/null
    fi
    MSG="AI-QE authored a test plan for ${KEY} (testplans/${KEY}.md) — awaiting review/approval. Approve with: make plan-approve KEY=${KEY}"
    # Tell the reviewer the plan was challenged and how it was resolved — an
    # invisible arbitration is worth nothing to the human doing the approving.
    if [ -n "$ADVERSARY_LINE" ]; then MSG="${MSG} (${ADVERSARY_LINE})"; fi
    if [ "$PR_PLAN" = "1" ]; then
      SCM comment "$REPO" "$PR" "$MSG" || true
      if [ -n "$PLAN_TICKET" ]; then TICKET_PLAN_COMMENT "$PLAN_TICKET" "$MSG"; fi
    else
      TICKET_PLAN_COMMENT "$KEY" "$MSG"
    fi
    NOTIFY post "$MSG" || true
    echo "PLAN_STATUS=DRAFT testplans/${KEY}.md"
    echo "$MSG"
    exit 0
  fi
  if python3 -c "import json,sys; c=json.load(open('out/testplan.contract.json')); sys.exit(0 if c.get('data_needs')=='none' else 1)" 2>/dev/null; then
    SKIP_PHASE testdata "plan declares data_needs: none"
  else
    PHASE testdata jira-testdata.md "$(CTX testdata)" out/testplan.contract.json
  fi
  GENERATE "$(CTX generate)" out/issue-guidance.md out/testplan.contract.json out/testdata.contract.json out/catalog-slice.jsonl "${IMPACT_CONTEXT[@]}" out/repo-conventions.md
fi
PHASE validate validate-repair.md out/generate.contract.json out/repo-conventions.md

relocate_artifacts

# Read-only semantic review of the generated source. Deliberately outside PHASE:
# a reviewer outage after paid generation/validation must not trigger a budget
# abort or suppress gate evidence. REVIEW_TESTS is total and never edits tests.
REVIEW_TESTS
REPAIR_FROM_REVIEW

# B3 delivery policy lives immediately before the gate boundary. The gate never
# reads reviewer output; under require, this pipeline stops before critic/gate,
# persists the refusal, and names the fixes on the same PR/ticket surfaces.
rm -f out/review-delivery.json
REVIEW_POLICY_RC=0
REVIEW_POLICY_LINE=""
# Preserve S3's default-off artifact parity: warn/off with no reviewer result
# creates no new delivery sidecar. Require forces REVIEW_TESTS on, so its
# approve/needs-work/unavailable evidence always reaches this decision.
if [ -f out/reviewer.contract.json ]; then
  REVIEW_POLICY_LINE=$(python3 engine/lib/test_reviewer.py enforce \
    out/reviewer.contract.json out/review-delivery.json) || REVIEW_POLICY_RC=$?
fi
if [ "$REVIEW_POLICY_RC" -eq 78 ]; then
  mkdir -p reports/runs
  REVIEW_LINE=$(python3 engine/lib/test_reviewer.py summary out/reviewer.contract.json || echo "")
  SUMMARY="AI-QE run ${RUN_ID} for ${KEY}: agent review refused delivery before the gate."
  if [ -n "$REVIEW_LINE" ]; then SUMMARY+=$'\n'"- ${REVIEW_LINE}"; fi
  SUMMARY+=$'\n'"- ${REVIEW_POLICY_LINE}"
  echo "[reviewer] $REVIEW_POLICY_LINE"
  if [ "$MODE" = "tests" ] && [ "$PR_PLAN" = "1" ]; then
    if [ -n "$PLAN_TICKET" ]; then TICKET_DELIVERY_COMMENT "$PLAN_TICKET" "$SUMMARY" "$REPO#$PR"; fi
  else
    case "$MODE" in
      jira|tests) TICKET_DELIVERY_COMMENT "$KEY" "$SUMMARY" ;;
      pr)
        if [ "$PR_TICKET_FUSED" = "1" ] && [ -n "$PLAN_TICKET" ] \
            && TICKET_RICH_ENABLED; then
          TICKET_DELIVERY_COMMENT "$PLAN_TICKET" "$SUMMARY" "$REPO#$PR"
        fi
        ;;
    esac
  fi
  write_run_record "$RUN_ID" "$MODE" "$KEY"
  NOTIFY post "$SUMMARY" || true
  if [ "$MODE" = "pr" ] || { [ "$MODE" = "tests" ] && [ "$PR_PLAN" = "1" ]; }; then
    HEAD_SHA=$(git -C "workspace/src/$REPO" rev-parse HEAD 2>/dev/null || echo "")
    if [ -n "$HEAD_SHA" ]; then SCM set_status "$REPO" "$HEAD_SHA" failure "AI-QE run ${RUN_ID}" || true; fi
    PR_COMMENT=$(python3 engine/lib/pr_comment.py "$RUN_ID" "$KEY" 2>/dev/null || true)
    if [ -n "$PR_COMMENT" ]; then SCM comment "$REPO" "$PR" "$PR_COMMENT" || true; fi
  fi
  exit 78
elif [ "$REVIEW_POLICY_RC" -ne 0 ]; then
  echo "AGENT_REVIEW_POLICY_ERROR: delivery decision could not be persisted; refusing before gate"
  exit 78
fi

# Critic (§5.8.7): an ADVISORY second opinion on test quality — vacuous assertions,
# duplicates, brittleness — which the deterministic gate structurally cannot judge.
# It runs read-only (org-config gives it no Write/Edit), it cannot repair, and NOTHING
# below reads its score to decide anything. Failures are swallowed on purpose: a critic
# outage must never quarantine an otherwise good run.
rm -f out/critic.contract.json
# Skip when there is nothing to score (5.1): zero generated tests means zero
# specs for the critic to review — a call that cannot change the outcome.
if python3 -c "import json,sys; c=json.load(open('out/generate.contract.json')); sys.exit(1 if c.get('tests') else 0)" 2>/dev/null; then
  SKIP_PHASE critic "no generated tests to score"
elif python3 engine/lib/critic.py enabled; then
  CRITIC_CTX=("$AIQE_P_AGENTS" out/generate.contract.json out/validate.contract.json)
  for extra in out/testplan.contract.json out/catalog-slice.jsonl out/coverage-gaps.md; do
    if [ -f "$extra" ]; then CRITIC_CTX+=("$extra"); fi
  done
  # Deliberately NOT via PHASE: the budget guard must not fire here. All the spend
  # already happened (generate/validate are done) — aborting with 77 between
  # validate and the gate would discard a fully-paid-for run over an advisory
  # signal. The critic's own cost is still metered for the record.
  CRITIC_RC=0
  _ARCHIVE_INPUTS "$RUN_ID" "$KEY" critic initial prompts/critic.md "${CRITIC_CTX[@]}"
  _PHASE_IMPL critic critic.md "${CRITIC_CTX[@]}" || CRITIC_RC=$?
  python3 engine/lib/budget.py record critic out/critic.json "$CRITIC_RC" || true
  if [ "$CRITIC_RC" -ne 0 ]; then
    echo "[critic] phase failed — advisory signal skipped, run continues"
    rm -f out/critic.contract.json
  fi
fi

# Per-test-repo gate; partial success is allowed and reported honestly (§5.8.5).
# Gates are independent (own repo dir, own app instance) — run them in PARALLEL.
SUMMARY="AI-QE run ${RUN_ID} for ${KEY}:"
: > out/gate_results.tsv
mkdir -p reports/runs out/gates
# Gate ONLY the repos resolved for THIS run — a glob over workspace/tests/*/ would
# re-gate (and commit under the wrong KEY) stale clones left by previous runs.
# Gate only the repos that actually CLONED; repos whose clone failed enter the
# results as clone_failed rows (run record marks the run for attention while
# every successfully-cloned repo still gets its commit — partial success).
if [ -s out/clone_failures.tsv ]; then
  cat out/clone_failures.tsv >> out/gate_results.tsv
  while IFS=$'\t' read -r cf_name _rest; do
    SUMMARY+=$'\n'"- ${cf_name}: clone failed ⚠ (skipped this run)"
  done < out/clone_failures.tsv
fi
GATE_NAMES=()
# The gate EXECUTES each test repo's own lint/test commands (docs/architecture.md
# S7 onboarding trust boundary), so an unbounded wait here is a hang nothing can
# end: budget.py checks MAX_WALLCLOCK_MIN BEFORE each phase, and the gate runs
# after the last phase. A hung gate holds out/.pipeline.lock until the 90-min
# stale break -- which frees the LOCK, not the process.
GATE_TO=()
if command -v timeout >/dev/null 2>&1; then
  # -k escalates to SIGKILL: SIGTERM alone is a REQUEST, and a lint/test
  # command that traps or ignores it keeps the gate (and this run) alive
  # exactly as if there were no timeout. SIGTERM first, so with-env.sh gets
  # to run its compose teardown -- timeout signals the whole process group,
  # so the trap does fire; only then the unconditional kill.
  GATE_TO=(timeout -k "${AIQE_GATE_KILL_AFTER_SEC:-30}" "${AIQE_GATE_TIMEOUT_SEC:-1200}")
else
  # C13: an unenforceable limit is never reported as an enforced one.
  echo "[pipeline] WARNING: no timeout(1) on PATH - gate runs are UNBOUNDED this run" >&2
fi
for name in $(cat out/cloned-tests.txt); do
  t="workspace/tests/$name/"
  GATE_NAMES+=("$name")
  (
    rc=0
    (cd "$t" && ${GATE_TO[@]+"${GATE_TO[@]}"} bash "$AIQE_ROOT/engine/gate/gate.sh" "$KEY" "$name") \
      > "out/gates/$name.out" 2>&1 || rc=$?
    echo "$rc" > "out/gates/$name.rc"
  ) &
done
wait
for name in "${GATE_NAMES[@]}"; do
  t="workspace/tests/$name/"
  GOUT=$(cat "out/gates/$name.out"); GRC=$(cat "out/gates/$name.rc")
  echo "$GOUT" | sed "s/^/[gate:$name] /"
  SHA=$(echo "$GOUT" | grep -oE "GATE_STATUS=COMMITTED [0-9a-f]+" | awk '{print $2}' || true)
  if [ $GRC -eq 0 ] && echo "$GOUT" | grep -q "GATE_STATUS=COMMITTED"; then
    SUMMARY+=$'\n'"- ${name}: committed ✅"; ST=committed
    EV gate.committed "$name" ok "key=$KEY sha=$SHA"
    # Archive the generated-test commit as a reviewable diff (workspace is ephemeral)
    git -C "$t" show HEAD > "reports/runs/${RUN_ID}-${name}.diff" 2>/dev/null || true
  elif [ $GRC -eq 0 ] && echo "$GOUT" | grep -q "GATE_STATUS=WOULD_COMMIT"; then
    # CHECK-ONLY. Every check passed and the gate was TOLD not to push, which is
    # the opposite of "there was nothing worth pushing" -- and this branch used
    # to record it as `no_changes`, because the gate exits 0 either way.
    # Reproduced before fixing: a full mock run with AIQE_GATE_CHECK_ONLY=1
    # produced overall=no_changes with both gates no_changes, so the permanent
    # record said the run produced nothing while the tests sat ready.
    # pipeline.sh never SETS the flag, but it never clears it either, so an
    # operator who left it in .env gets this on every run.
    SUMMARY+=$'\n'"- ${name}: would commit (checks passed; AIQE_GATE_CHECK_ONLY is set, so nothing was pushed)"
    ST=would_commit
    EV gate.would_commit "$name" ok "key=$KEY"
  elif [ $GRC -eq 0 ]; then
    SUMMARY+=$'\n'"- ${name}: no changes ➖"; ST=no_changes
    EV gate.no_changes "$name" ok "key=$KEY"
  else
    if [ "$GRC" -eq 124 ] || [ "$GRC" -eq 137 ]; then
      _how="timed out (exit 124)"
      [ "$GRC" -eq 137 ] && _how="ignored the request to stop and was KILLED (exit 137)"
      SUMMARY+=$'\n'"- ${name}: gate ${_how} after ${AIQE_GATE_TIMEOUT_SEC:-1200}s - nothing was established about these tests, and nothing was committed"
    else
      SUMMARY+=$'\n'"- ${name}: quarantined ❌ (exit $GRC, see reports)"
    fi
    ST=quarantined
    EV gate.refused "$name" refused "key=$KEY exit=$GRC"
  fi
  printf '%s\t%s\t%s\t%s\n' "$name" "$ST" "$GRC" "$SHA" >> out/gate_results.tsv
done
# A6: the commit is now an established fact. Parse only committed changed specs
# into the derived testcase store before the durable run record is assembled.
# An index outage cannot undo or reclassify the gate commit; it is persisted as
# `unavailable` in out/learning-loop.json and surfaced on this run's summary.
LEARNING_RC=0
python3 engine/lib/testcase_learning.py index "$RUN_ID" "$KEY" || LEARNING_RC=$?
if [ "$LEARNING_RC" -ne 0 ]; then
  SUMMARY+=$'\n'"- testcase learning unavailable ⚠ (commits remain valid; retry indexing)"
fi
# Advisory critic line on the summary, so the score reaches the reviewer with the
# artifacts rather than only in the run record. Appended AFTER the gate loop by
# design — the commit decision above is already final and independent of it.
if [ -f out/critic.contract.json ]; then
  CRITIC_LINE=$(python3 engine/lib/critic.py record "$KEY" || echo "")
  if [ -n "$CRITIC_LINE" ]; then
    echo "[critic] $CRITIC_LINE"
    SUMMARY+=$'\n'"- ${CRITIC_LINE} (advisory — does not gate)"
  fi
fi
# B4: PR and JIRA summaries use the same bounded review projection as the run
# record. Disabled and unavailable are explicit verdicts, never silent absence.
REVIEW_LINE=$(python3 engine/lib/test_reviewer.py summary out/reviewer.contract.json || echo "")
if [ -n "$REVIEW_LINE" ]; then
  echo "[reviewer] $REVIEW_LINE"
  SUMMARY+=$'\n'"- ${REVIEW_LINE}"
fi
# Best-effort notifications: an unreachable tracker/Slack must not abort the run
# before the run record, build status, and review-state transition are persisted.
# tests mode is the plan-first resume of a JIRA ticket — the ticket gets the summary too
if [ "$MODE" = "tests" ] && [ "$PR_PLAN" = "1" ]; then
  if [ -n "$PLAN_TICKET" ]; then TICKET_DELIVERY_COMMENT "$PLAN_TICKET" "$SUMMARY" "$REPO#$PR"; fi
else
  case "$MODE" in
    jira|tests) TICKET_DELIVERY_COMMENT "$KEY" "$SUMMARY" ;;
    pr)
      if [ "$PR_TICKET_FUSED" = "1" ] && [ -n "$PLAN_TICKET" ] \
          && TICKET_RICH_ENABLED; then
        TICKET_DELIVERY_COMMENT "$PLAN_TICKET" "$SUMMARY" "$REPO#$PR"
      fi
      ;;
  esac
fi
NOTIFY post "$SUMMARY" || true
# P0: surface the outcome as a build status on the PR head (merge-gate visibility)
if [ "$MODE" = "pr" ] || { [ "$MODE" = "tests" ] && [ "$PR_PLAN" = "1" ]; }; then
  HEAD_SHA=$(git -C "workspace/src/$REPO" rev-parse HEAD 2>/dev/null || echo "")
  STATE=success; echo "$SUMMARY" | grep -q quarantined && STATE=failure
  if [ -n "$HEAD_SHA" ]; then SCM set_status "$REPO" "$HEAD_SHA" "$STATE" "AI-QE run ${RUN_ID}" || true; fi
  # Coverage-delta comment ON the PR (product-direction H1): behaviors covered,
  # created-vs-updated tests, validation, gate outcome, open questions. Composed
  # from this run's own out/ artifacts; empty (no comment) when triage found no
  # E2E impact, so PRs never accumulate noise. Best-effort like every notify.
  PR_COMMENT=$(python3 engine/lib/pr_comment.py "$RUN_ID" "$KEY" 2>/dev/null || true)
  if [ -n "$PR_COMMENT" ]; then SCM comment "$REPO" "$PR" "$PR_COMMENT" || true; fi
fi
# Run record: persisted for QA monitoring (reports/runs/) AND emitted as telemetry
write_run_record "$RUN_ID" "$MODE" "$KEY"
# Team-review tracking: committed artifacts put the key into pending_review
python3 engine/lib/review_state.py auto "$KEY"
# Plan provenance: record which run generated tests from the approved plan
if [ "$MODE" = "tests" ]; then
  python3 engine/lib/plan_state.py generated "$KEY" "$RUN_ID" > /dev/null || true
fi
