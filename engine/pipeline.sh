#!/usr/bin/env bash
# Core pipeline entry (Path 1/2/3 all call this). architecture §5.3, §5.8
# Usage: pipeline.sh pr <source_repo> <pr_number> | pipeline.sh jira <KEY>
set -euo pipefail
MODE=${1:?pr|jira}; export AIQE_ROOT="$PWD"; mkdir -p out workspace
source .env 2>/dev/null || true
if [ "${AIQE_MOCK:-0}" = "1" ]; then
  SCM() { bash adapters/mock/scm.sh "$@"; }
  TRACKER() { bash adapters/mock/tracker.sh "$@"; }
  NOTIFY() { bash adapters/mock/notify.sh "$@"; }
  TELEM() { bash adapters/mock/telemetry.sh "$@"; }
  PHASE() { bash engine/phases/mock_phase.sh "$1" "$KEY" workspace; }
else
  SCM() { bash "$(python3 -c "import yaml;print(yaml.safe_load(open('registry/org-config.yaml'))['adapters']['scm']['${SCM_KIND:-github}'])")" "$@"; }
  TRACKER() { bash adapters/tracker/jira.sh "$@"; }
  NOTIFY() { bash adapters/notify/slack.sh "$@"; }
  TELEM() { bash adapters/telemetry/splunk.sh "$@"; }
  PHASE() { bash engine/phases/run_phase.sh "$1" "prompts/$2" workspace "${@:3}"; }
fi

RUN_ID=$(date +%s)-$RANDOM
if [ "$MODE" = "pr" ]; then
  REPO=$2; PR=$3; KEY="PR-${REPO}-${PR}"
  SCM changed_files "$REPO" "$PR" > out/changed.txt
  python3 engine/phases/resolve.py pr "$REPO" --changed-files out/changed.txt > out/resolve.contract.json
else
  KEY=$2
  TRACKER get_item "$KEY" > out/ticket.json
  COMP=$(python3 -c "import json;t=json.load(open('out/ticket.json'));print(','.join(t.get('components',[])))")
  LBL=$(python3 -c "import json;t=json.load(open('out/ticket.json'));print(','.join(t.get('labels',[])))")
  LINKED=$(python3 -c "import json;t=json.load(open('out/ticket.json'));print(','.join(t.get('linked_repos',[])))")
  python3 engine/phases/resolve.py jira "$KEY" --components "$COMP" --labels "$LBL" --linked-repos "$LINKED" > out/resolve.contract.json
  # Knowledge port: pull linked Confluence pages (budgeted) as analyze context
  if [ "${AIQE_MOCK:-0}" = "1" ]; then echo "## Linked PRD (mock): discounts must be 1-90%" > out/confluence.md; \
  else bash adapters/knowledge/confluence.sh get_linked_docs out/ticket.json > out/confluence.md || true; fi
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

# Phase chain (Workflow A: triage->generate->validate; B: analyze->plan->data->generate->validate)
if [ "$MODE" = "pr" ]; then
  PHASE triage   pr-triage.md    AGENTS.md out/resolve.contract.json
  PHASE generate pr-generate.md  AGENTS.md out/triage.contract.json
else
  PHASE analyze  jira-analyze.md AGENTS.md out/ticket.json out/confluence.md
  PHASE testplan jira-testplan.md AGENTS.md out/analyze.contract.json
  PHASE testdata jira-testdata.md AGENTS.md out/testplan.contract.json
  PHASE generate pr-generate.md  AGENTS.md out/testplan.contract.json out/testdata.contract.json
fi
PHASE validate validate-repair.md out/generate.contract.json

# Per-test-repo gate; partial success is allowed and reported honestly (§5.8.5)
SUMMARY="AI-QE run ${RUN_ID} for ${KEY}:"
: > out/gate_results.tsv
mkdir -p reports/runs
for t in workspace/tests/*/; do
  name=$(basename "$t")
  GOUT=$( (cd "$t" && bash "$AIQE_ROOT/engine/gate/gate.sh" "$KEY" "$name") 2>&1 ) && GRC=0 || GRC=$?
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
[ "$MODE" = "jira" ] && TRACKER comment "$KEY" "$SUMMARY"
NOTIFY post "$SUMMARY"
# Run record: persisted for QA monitoring (reports/runs/) AND emitted as telemetry
python3 engine/lib/run_record.py "$RUN_ID" "$MODE" "$KEY" \
  | tee "reports/runs/${RUN_ID}.json" | TELEM emit_event
