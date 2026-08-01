#!/usr/bin/env bash
# Core pipeline entry (Path 1/2/3 all call this). architecture §5.3, §5.8
# Usage: pipeline.sh pr <source_repo> <pr_number> | pipeline.sh jira <KEY>
#        pipeline.sh plan  <KEY>   author the test plan ONLY, then stop for human
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
ACQUIRED=0
for i in $(seq 1 120); do
  if mkdir "$LOCK" 2>/dev/null; then trap 'rmdir "$LOCK" 2>/dev/null' EXIT; ACQUIRED=1; break; fi
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +90 2>/dev/null)" ]; then rmdir "$LOCK" 2>/dev/null || true; fi
  sleep 1
done
if [ "$ACQUIRED" != "1" ]; then echo "PIPELINE_BUSY: another run holds $LOCK"; exit 75; fi
if [ "${AIQE_MOCK:-0}" = "1" ]; then
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
      out/phase-skips.tsv
_budget_guard() {
  local why
  if ! why=$(python3 engine/lib/budget.py check --start "$RUN_START"); then
    echo "$why"
    local msg="AI-QE run ${RUN_ID:-?} for ${KEY:-?} ABORTED before phase '$1': $why"
    case "${MODE:-}" in jira|plan|tests) TRACKER comment "$KEY" "$msg" || true ;; esac
    NOTIFY post "$msg" || true
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
PHASE() {
  local label="${AIQE_PHASE_LABEL:-$1}"
  _budget_guard "$label"
  local rc=0
  _PHASE_IMPL "$@" || rc=$?
  python3 engine/lib/budget.py record "$label" "out/$label.json" || true
  # Context-retry escape hatch (cost-reduction 2.3): a phase that ran on a
  # SCOPED context and reported `missing_context` gets ONE re-run with the full
  # estate — the miss is recorded so the scoping policy can be tuned instead of
  # silently degrading output.
  if [ "$rc" -eq 0 ] && [ "${AIQE_CONTEXT_RETRY:-1}" != "0" ]; then
    if python3 -c "import json,sys; c=json.load(open('out/${label}.contract.json')); sys.exit(0 if c.get('missing_context') else 1)" 2>/dev/null; then
      local args=() f swapped=0
      for f in "$@"; do
        case "$f" in out/context-*.md) args+=("AGENTS.md"); swapped=1 ;;
                     *) args+=("$f") ;; esac
      done
      if [ "$swapped" = "1" ]; then
        echo "[context] $label reported missing context — one retry with the full estate"
        python3 -c "import json; c=json.load(open('out/${label}.contract.json')); print('${label}\t' + '; '.join(map(str, c.get('missing_context') or [])))" >> out/context-retries.tsv 2>/dev/null || true
        _PHASE_IMPL "${args[@]}" || rc=$?
        python3 engine/lib/budget.py record "$label" "out/$label.json" || true
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
  if python3 engine/lib/context_scope.py assemble "$phase" >/dev/null 2>&1 \
     && [ -s "out/context-${phase}.md" ]; then
    echo "out/context-${phase}.md"
  else
    echo "AGENTS.md"
  fi
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
    python3 engine/lib/spec_exemplars.py "$conv" "$repo" > /dev/null 2>&1 || : > "$conv"
    [ -f "$conv" ] || : > "$conv"
    # This repo's own existing-test rows, plus anything covering the app repos
    # this run touched — so the agent extends its own suite instead of
    # duplicating, and knows what is already covered elsewhere. Same reason the
    # conventions are per-repo: an agent writing into ONE repo should not be
    # reasoning over every other repo's catalog.
    slice="out/catalog-slice-${repo}.jsonl"
    python3 engine/lib/catalog_slice.py out/resolve.contract.json "$repo" \
      > "$slice" 2>/dev/null || cp out/catalog-slice.jsonl "$slice" 2>/dev/null || : > "$slice"
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

RUN_ID=$(date +%s)-$RANDOM
if [ "$MODE" = "pr" ]; then
  REPO=$2; PR=$3; export KEY="PR-${REPO}-${PR}"
  case "$KEY" in *[!A-Za-z0-9._-]*) echo "INVALID_KEY: $KEY"; exit 64;; esac
  SCM changed_files "$REPO" "$PR" > out/changed.txt
  # P0: the actual patch, not just the file list — triage reviews real hunks
  SCM diff "$REPO" "$PR" > out/pr.diff 2>/dev/null || : > out/pr.diff
  python3 engine/phases/resolve.py pr "$REPO" --changed-files out/changed.txt > out/resolve.contract.json
else
  export KEY=$2
  case "$KEY" in *[!A-Za-z0-9._-]*|"") echo "INVALID_KEY: $KEY"; exit 64;; esac
  # Generation from a plan is gated on human approval — check BEFORE any clone/LLM work
  if [ "$MODE" = "tests" ]; then
    python3 engine/lib/plan_state.py require-approved "$KEY"
  fi
  # SDD 2.2: when the requirements gate is ON, planning requires validated
  # requirements — checked BEFORE any clone/LLM work, like the plan gate.
  if [ "$MODE" = "plan" ] || [ "$MODE" = "jira" ]; then
    python3 engine/lib/plan_state.py require-requirements "$KEY"
  fi
  # P0: inline JIRA context ("pass JIRA context as text input") bypasses the tracker
  if [ -n "${AIQE_INLINE_FILE:-}" ]; then
    cp "$AIQE_INLINE_FILE" out/ticket.json
  else
    TRACKER get_item "$KEY" > out/ticket.json
  fi
  COMP=$(python3 -c "import json;t=json.load(open('out/ticket.json'));print(','.join(t.get('components',[])))")
  LBL=$(python3 -c "import json;t=json.load(open('out/ticket.json'));print(','.join(t.get('labels',[])))")
  LINKED=$(python3 -c "import json;t=json.load(open('out/ticket.json'));print(','.join(t.get('linked_repos',[])))")
  python3 engine/phases/resolve.py jira "$KEY" --components "$COMP" --labels "$LBL" --linked-repos "$LINKED" > out/resolve.contract.json
  # Release tracking: capture the ticket's fixVersions as the key's target release
  FIXV=$(python3 -c "import json;t=json.load(open('out/ticket.json'));print(','.join(t.get('fix_versions',[])))")
  if [ -n "$FIXV" ]; then python3 engine/lib/review_state.py release "$KEY" "$FIXV" jira; fi
  # Knowledge port: pull linked Confluence pages (budgeted) as analyze context
  if [ "${AIQE_MOCK:-0}" = "1" ]; then echo "## Linked PRD (mock): discounts must be 1-90%" > out/confluence.md; \
  else bash adapters/knowledge/confluence.sh get_linked_docs out/ticket.json > out/confluence.md || true; fi
  # P0: issue-type-aware generation — bug fixes get regression guidance,
  # security fixes get negative/abuse-case guidance, stories the extend-first bias
  ITYPE=$(python3 -c "import json;t=json.load(open('out/ticket.json'));print((t.get('issue_type') or 'story').lower())")
  GUID=prompts/issue-types/story.md
  case "$ITYPE" in *bug*|*defect*) GUID=prompts/issue-types/bug.md ;; \
                   *security*|*vulnerab*) GUID=prompts/issue-types/security.md ;; esac
  if echo "$LBL" | grep -qi security; then GUID=prompts/issue-types/security.md; fi
  cp "$GUID" out/issue-guidance.md
fi

if [ "$(python3 -c "import json;print(json.load(open('out/resolve.contract.json')).get('needs_clarification', False))")" = "True" ]; then
  MSG="AI-QE cannot confidently route ${KEY}. Candidates: $(cat out/resolve.contract.json). Reply with '@openhands use <repos>'."
  case "$MODE" in jira|plan|tests) TRACKER comment "$KEY" "$MSG" || true ;; esac
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
  SCM clone_ro "$r" "workspace/src/$r"
done
# Partial success starts HERE (§5.8.5): one test repo whose clone fails — bad
# credentials, renamed slug, a mapped repo with no material yet — must not kill
# the work every OTHER repo would get. Failed clones are skipped with a
# clone_failed gate row so the run record and summary stay honest.
: > out/cloned-tests.txt
: > out/clone_failures.tsv
for t in $(python3 -c "import json;print(' '.join(json.load(open('out/resolve.contract.json'))['test_repos']))"); do
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
    for _cf in catalog/*.jsonl; do
      [ -f "$_cf" ] || continue
      [ "$(basename "$_cf")" = "catalog.sample.jsonl" ] && continue
      grep -h . "$_cf" >> out/catalog-slice.jsonl 2>/dev/null || true
    done
  }
# Coverage gaps: surface with NO test evidence — generation targets these first
python3 engine/lib/coverage_gaps.py md > out/coverage-gaps.md 2>/dev/null || : > out/coverage-gaps.md
# Existing-approach exemplars: REAL helper + spec code from each resolved test repo,
# so generated tests mirror the repo's own approach instead of inventing a new one.
python3 engine/lib/spec_exemplars.py out/repo-conventions.md \
  $(python3 -c "import json;print(' '.join(json.load(open('out/resolve.contract.json'))['test_repos']))") \
  > /dev/null 2>&1 || : > out/repo-conventions.md
[ -f out/repo-conventions.md ] || : > out/repo-conventions.md

# Control-repo artifacts (test plans, canonical data) belong at the root; real phases
# run with cwd=workspace so relocate anything written there (no-op in mock mode).
relocate_artifacts() {
  for d in testplans testdata; do
    if [ -d "workspace/$d" ]; then mkdir -p "$d"; cp -r "workspace/$d/." "$d/"; rm -rf "workspace/$d"; fi
  done
}

# Phase chain (Workflow A: triage->generate->validate; B: analyze->plan->data->generate->validate)
if [ "$MODE" = "pr" ]; then
  PHASE triage   pr-triage.md    "$(CTX triage)" out/resolve.contract.json out/changed.txt out/pr.diff out/catalog-slice.jsonl out/coverage-gaps.md
  # Extend-vs-create scout (roadmap 2.1): deterministic join of the diff's surface
  # against catalog evidence, emitting NAMED extend targets. Tolerant — a scout
  # failure yields an empty file, never a failed run.
  python3 engine/lib/extend_scout.py > out/extend-candidates.md 2>/dev/null || : > out/extend-candidates.md
  GENERATE "$(CTX generate)" out/triage.contract.json out/pr.diff out/catalog-slice.jsonl out/extend-candidates.md out/coverage-gaps.md out/repo-conventions.md
elif [ "$MODE" = "tests" ]; then
  # Resume from the APPROVED plan. The reviewed markdown is authoritative (it may have
  # been edited), so it is passed to both phases alongside the snapshotted contract.
  # The snapshot must exist (plan mode wrote it) — a silent fallback here would let
  # a stale contract from a different key shape generation.
  if ! cp "reports/plans/${KEY}.contract.json" out/testplan.contract.json 2>/dev/null; then
    echo "PLAN_SNAPSHOT_MISSING: reports/plans/${KEY}.contract.json — re-run 'pipeline.sh plan ${KEY}'"
    exit 64
  fi
  if python3 -c "import json,sys; c=json.load(open('out/testplan.contract.json')); sys.exit(0 if c.get('data_needs')=='none' else 1)" 2>/dev/null; then
    SKIP_PHASE testdata "plan declares data_needs: none"
  else
    PHASE testdata jira-testdata.md "$(CTX testdata)" out/testplan.contract.json "testplans/${KEY}.md"
  fi
  GENERATE "$(CTX generate)" out/issue-guidance.md out/testplan.contract.json out/testdata.contract.json "testplans/${KEY}.md" out/catalog-slice.jsonl out/repo-conventions.md
else
  PHASE analyze  jira-analyze.md "$(CTX analyze)" out/issue-guidance.md out/ticket.json out/confluence.md
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
    { TRACKER comment "$KEY" "$RQ_MSG"; } || true
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
    { TRACKER comment "$KEY" "$CL_MSG"; } || true
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
      out/testplan.contract.json "testplans/${KEY}.md" out/coverage-gaps.md || ADV_RC=$?
    if [ "$ADV_RC" -ne 0 ]; then
      echo "[plan-adversary] phase failed — the authored plan stands"
      rm -f out/planadversary.contract.json
    elif [ -f out/planadversary.contract.json ]; then
      ARB_RC=0
      PHASE planarbiter jira-plan-arbitrate.md "$(CTX planarbiter)" out/analyze.contract.json \
        out/testplan.contract.json out/planadversary.contract.json \
        "testplans/${KEY}.md" out/resolve.contract.json || ARB_RC=$?
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
  if [ "$MODE" = "plan" ]; then
    # STOP: the plan awaits human review/edit/approval. No test code, no commit.
    relocate_artifacts
    python3 engine/lib/plan_state.py record "$KEY" out/testplan.contract.json "$ADVERSARY_LINE" > /dev/null
    MSG="AI-QE authored a test plan for ${KEY} (testplans/${KEY}.md) — awaiting review/approval. Approve with: make plan-approve KEY=${KEY}"
    # Tell the reviewer the plan was challenged and how it was resolved — an
    # invisible arbitration is worth nothing to the human doing the approving.
    if [ -n "$ADVERSARY_LINE" ]; then MSG="${MSG} (${ADVERSARY_LINE})"; fi
    { TRACKER comment "$KEY" "$MSG"; } || true
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
  GENERATE "$(CTX generate)" out/issue-guidance.md out/testplan.contract.json out/testdata.contract.json out/catalog-slice.jsonl out/repo-conventions.md
fi
PHASE validate validate-repair.md out/generate.contract.json out/repo-conventions.md

relocate_artifacts

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
  CRITIC_CTX=(AGENTS.md out/generate.contract.json out/validate.contract.json)
  for extra in out/testplan.contract.json out/catalog-slice.jsonl out/coverage-gaps.md; do
    if [ -f "$extra" ]; then CRITIC_CTX+=("$extra"); fi
  done
  # Deliberately NOT via PHASE: the budget guard must not fire here. All the spend
  # already happened (generate/validate are done) — aborting with 77 between
  # validate and the gate would discard a fully-paid-for run over an advisory
  # signal. The critic's own cost is still metered for the record.
  CRITIC_RC=0
  _PHASE_IMPL critic critic.md "${CRITIC_CTX[@]}" || CRITIC_RC=$?
  python3 engine/lib/budget.py record critic out/critic.json || true
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
for name in $(cat out/cloned-tests.txt); do
  t="workspace/tests/$name/"
  GATE_NAMES+=("$name")
  (
    rc=0
    (cd "$t" && bash "$AIQE_ROOT/engine/gate/gate.sh" "$KEY" "$name") \
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
    # Archive the generated-test commit as a reviewable diff (workspace is ephemeral)
    git -C "$t" show HEAD > "reports/runs/${RUN_ID}-${name}.diff" 2>/dev/null || true
  elif [ $GRC -eq 0 ]; then
    SUMMARY+=$'\n'"- ${name}: no changes ➖"; ST=no_changes
  else
    SUMMARY+=$'\n'"- ${name}: quarantined ❌ (exit $GRC, see reports)"; ST=quarantined
  fi
  printf '%s\t%s\t%s\t%s\n' "$name" "$ST" "$GRC" "$SHA" >> out/gate_results.tsv
done
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
# Best-effort notifications: an unreachable tracker/Slack must not abort the run
# before the run record, build status, and review-state transition are persisted.
# tests mode is the plan-first resume of a JIRA ticket — the ticket gets the summary too
case "$MODE" in jira|tests) TRACKER comment "$KEY" "$SUMMARY" || true ;; esac
NOTIFY post "$SUMMARY" || true
# P0: surface the outcome as a build status on the PR head (merge-gate visibility)
if [ "$MODE" = "pr" ]; then
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
python3 engine/lib/run_record.py "$RUN_ID" "$MODE" "$KEY" \
  | tee "reports/runs/${RUN_ID}.json" | TELEM emit_event
# Team-review tracking: committed artifacts put the key into pending_review
python3 engine/lib/review_state.py auto "$KEY"
# Plan provenance: record which run generated tests from the approved plan
if [ "$MODE" = "tests" ]; then
  python3 engine/lib/plan_state.py generated "$KEY" "$RUN_ID" > /dev/null || true
fi
