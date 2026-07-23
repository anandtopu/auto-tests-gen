#!/usr/bin/env bash
# Core pipeline entry (Path 1/2/3 all call this). architecture §5.3, §5.8
# Usage: pipeline.sh pr <source_repo> <pr_number> | pipeline.sh jira <KEY>
#        pipeline.sh plan  <KEY>   author the test plan ONLY, then stop for human
#                                  review/edit/approval (no test code, no commit)
#        pipeline.sh tests <KEY>   resume from an APPROVED plan: data -> generate ->
#                                  validate -> gate
set -euo pipefail
MODE=${1:?pr|jira|plan|tests}; export AIQE_ROOT="$PWD"; mkdir -p out workspace
# .env supplies defaults only — an explicitly-set caller AIQE_MOCK (make demo-* =1,
# make run-* =0, queue workers) must never be silently inverted by the file.
_PRE_MOCK="${AIQE_MOCK:-}"
# Config layers, lowest first: aiqe.properties < .env < explicit environment.
# shell-defaults only emits keys absent from the environment, so exporting a variable
# still wins; `source .env` runs after, so .env overrides the properties baseline.
eval "$(python3 engine/lib/props_file.py shell-defaults 2>/dev/null || true)"
source .env 2>/dev/null || true
if [ -n "$_PRE_MOCK" ]; then AIQE_MOCK="$_PRE_MOCK"; fi

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
    PHASE() { bash engine/phases/run_phase.sh "$1" "prompts/$2" workspace "${@:3}"; }
  else
    PHASE() { bash engine/phases/mock_phase.sh "$1" "$KEY" workspace; }
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
  PHASE() { bash engine/phases/run_phase.sh "$1" "prompts/$2" workspace "${@:3}"; }
fi

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
  [ "$MODE" = "jira" ] && TRACKER comment "$KEY" "$MSG"; NOTIFY post "$MSG"
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

# Catalog slice: existing-test knowledge handed to the phases (P2)
grep -h . catalog/e2e-*.jsonl 2>/dev/null > out/catalog-slice.jsonl || true
# Coverage gaps: surface with NO test evidence — generation targets these first
python3 engine/lib/coverage_gaps.py md > out/coverage-gaps.md 2>/dev/null || : > out/coverage-gaps.md

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
  PHASE generate pr-generate.md  AGENTS.md out/triage.contract.json out/pr.diff out/coverage-gaps.md
elif [ "$MODE" = "tests" ]; then
  # Resume from the APPROVED plan. The reviewed markdown is authoritative (it may have
  # been edited), so it is passed to both phases alongside the snapshotted contract.
  cp "reports/plans/${KEY}.contract.json" out/testplan.contract.json 2>/dev/null || true
  PHASE testdata jira-testdata.md AGENTS.md out/testplan.contract.json "testplans/${KEY}.md"
  PHASE generate pr-generate.md  AGENTS.md out/issue-guidance.md out/testplan.contract.json out/testdata.contract.json "testplans/${KEY}.md"
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
  PHASE generate pr-generate.md  AGENTS.md out/issue-guidance.md out/testplan.contract.json out/testdata.contract.json
fi
PHASE validate validate-repair.md out/generate.contract.json

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
  PHASE critic critic.md "${CRITIC_CTX[@]}" || {
    echo "[critic] phase failed — advisory signal skipped, run continues"
    rm -f out/critic.contract.json
  }
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
{ [ "$MODE" = "jira" ] && TRACKER comment "$KEY" "$SUMMARY"; } || true
NOTIFY post "$SUMMARY" || true
# P0: surface the outcome as a build status on the PR head (merge-gate visibility)
if [ "$MODE" = "pr" ]; then
  HEAD_SHA=$(git -C "workspace/src/$REPO" rev-parse HEAD 2>/dev/null || echo "")
  STATE=success; echo "$SUMMARY" | grep -q quarantined && STATE=failure
  if [ -n "$HEAD_SHA" ]; then SCM set_status "$REPO" "$HEAD_SHA" "$STATE" "AI-QE run ${RUN_ID}" || true; fi
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
