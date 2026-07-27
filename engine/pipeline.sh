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
case "$MODE" in pr|jira|plan|tests) ;; *) echo "INVALID_MODE: $MODE (pr|jira|plan|tests)"; exit 64 ;; esac
# Config layers, lowest first: aiqe.properties < .env < explicit environment.
# Both emitters print `export K='v'` lines ONLY for keys absent from the
# environment, so an explicitly-exported variable always wins (the file can never
# invert a caller's AIQE_MOCK), and — unlike the old `source .env` — every value
# is EXPORTED, so adapters, phases and python children actually see it.
# .env is applied FIRST: first-fill wins, so .env beats the properties baseline
# (the Settings page writes .env — a UI save must never be masked by properties).
eval "$(python3 engine/lib/props_file.py dotenv-defaults 2>/dev/null || true)"
eval "$(python3 engine/lib/props_file.py shell-defaults 2>/dev/null || true)"

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
rm -f "${AIQE_COST_LEDGER:-out/cost.tsv}" out/*.json out/gate_results.tsv
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
PHASE() {
  _budget_guard "$1"
  local rc=0
  _PHASE_IMPL "$@" || rc=$?
  python3 engine/lib/budget.py record "$1" "out/$1.json" || true
  return $rc
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
for t in $(python3 -c "import json;print(' '.join(json.load(open('out/resolve.contract.json'))['test_repos']))"); do
  SCM clone_rw "$t" "workspace/tests/$t" "test/${KEY}-ai-qe"
done

# Refresh estate knowledge from the just-cloned sources so every LLM phase sees
# CURRENT contracts/routes/coverage (AGENTS.md is passed as phase context below).
python3 bin/gen_agents_md.py > /dev/null || true

# Catalog slice: existing-test knowledge handed to the phases (P2). Any test-repo
# name is valid (bootstrap writes catalog/<repo>.jsonl) — only the committed sample
# is excluded, matching every Python reader's glob.
: > out/catalog-slice.jsonl
for _cf in catalog/*.jsonl; do
  [ -f "$_cf" ] || continue
  [ "$(basename "$_cf")" = "catalog.sample.jsonl" ] && continue
  grep -h . "$_cf" >> out/catalog-slice.jsonl 2>/dev/null || true
done
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
  PHASE triage   pr-triage.md    AGENTS.md out/resolve.contract.json out/changed.txt out/pr.diff out/catalog-slice.jsonl out/coverage-gaps.md
  PHASE generate pr-generate.md  AGENTS.md out/triage.contract.json out/pr.diff out/coverage-gaps.md out/repo-conventions.md
elif [ "$MODE" = "tests" ]; then
  # Resume from the APPROVED plan. The reviewed markdown is authoritative (it may have
  # been edited), so it is passed to both phases alongside the snapshotted contract.
  # The snapshot must exist (plan mode wrote it) — a silent fallback here would let
  # a stale contract from a different key shape generation.
  if ! cp "reports/plans/${KEY}.contract.json" out/testplan.contract.json 2>/dev/null; then
    echo "PLAN_SNAPSHOT_MISSING: reports/plans/${KEY}.contract.json — re-run 'pipeline.sh plan ${KEY}'"
    exit 64
  fi
  PHASE testdata jira-testdata.md AGENTS.md out/testplan.contract.json "testplans/${KEY}.md"
  PHASE generate pr-generate.md  AGENTS.md out/issue-guidance.md out/testplan.contract.json out/testdata.contract.json "testplans/${KEY}.md" out/repo-conventions.md
else
  PHASE analyze  jira-analyze.md AGENTS.md out/issue-guidance.md out/ticket.json out/confluence.md
  PHASE testplan jira-testplan.md AGENTS.md out/issue-guidance.md out/analyze.contract.json out/coverage-gaps.md
  if [ "$MODE" = "plan" ]; then
    # STOP: the plan awaits human review/edit/approval. No test code, no commit.
    relocate_artifacts
    python3 engine/lib/plan_state.py record "$KEY" out/testplan.contract.json > /dev/null
    MSG="AI-QE authored a test plan for ${KEY} (testplans/${KEY}.md) — awaiting review/approval. Approve with: make plan-approve KEY=${KEY}"
    { TRACKER comment "$KEY" "$MSG"; } || true
    NOTIFY post "$MSG" || true
    echo "PLAN_STATUS=DRAFT testplans/${KEY}.md"
    echo "$MSG"
    exit 0
  fi
  PHASE testdata jira-testdata.md AGENTS.md out/testplan.contract.json
  PHASE generate pr-generate.md  AGENTS.md out/issue-guidance.md out/testplan.contract.json out/testdata.contract.json out/repo-conventions.md
fi
PHASE validate validate-repair.md out/generate.contract.json out/repo-conventions.md

relocate_artifacts

# Critic (§5.8.7): an ADVISORY second opinion on test quality — vacuous assertions,
# duplicates, brittleness — which the deterministic gate structurally cannot judge.
# It runs read-only (org-config gives it no Write/Edit), it cannot repair, and NOTHING
# below reads its score to decide anything. Failures are swallowed on purpose: a critic
# outage must never quarantine an otherwise good run.
rm -f out/critic.contract.json
if python3 engine/lib/critic.py enabled; then
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
GATE_NAMES=()
for name in $(python3 -c "import json;print(' '.join(json.load(open('out/resolve.contract.json'))['test_repos']))"); do
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
