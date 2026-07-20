#!/usr/bin/env bash
# Core pipeline entry (Path 1/2/3 all call this). architecture §5.3, §5.8
# Usage: pipeline.sh pr <source_repo> <pr_number> | pipeline.sh jira <KEY>
set -euo pipefail
MODE=${1:?pr|jira}; export AIQE_ROOT="$PWD"; mkdir -p out workspace
source .env 2>/dev/null || true
SCM() { bash "$(python3 -c "import yaml;print(yaml.safe_load(open('registry/org-config.yaml'))['adapters']['scm']['${SCM_KIND:-github}'])")" "$@"; }
TRACKER() { bash adapters/tracker/jira.sh "$@"; }
NOTIFY() { bash adapters/notify/slack.sh "$@"; }
TELEM() { bash adapters/telemetry/splunk.sh "$@"; }

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
  bash adapters/knowledge/confluence.sh get_linked_docs out/ticket.json > out/confluence.md || true
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

# Phase chain (Workflow A: triage->generate->validate; B: analyze->plan->data->generate->validate)
if [ "$MODE" = "pr" ]; then
  bash engine/phases/run_phase.sh triage   prompts/pr-triage.md    workspace out/resolve.contract.json
  bash engine/phases/run_phase.sh generate prompts/pr-generate.md  workspace out/triage.contract.json
else
  bash engine/phases/run_phase.sh analyze  prompts/jira-analyze.md workspace out/ticket.json out/confluence.md
  bash engine/phases/run_phase.sh testplan prompts/jira-testplan.md workspace out/analyze.contract.json
  bash engine/phases/run_phase.sh testdata prompts/jira-testdata.md workspace out/testplan.contract.json
  bash engine/phases/run_phase.sh generate prompts/pr-generate.md  workspace out/testplan.contract.json out/testdata.contract.json
fi
bash engine/phases/run_phase.sh validate prompts/validate-repair.md workspace out/generate.contract.json

# Per-test-repo gate; partial success is allowed and reported honestly (§5.8.5)
SUMMARY="AI-QE run ${RUN_ID} for ${KEY}:"
for t in workspace/tests/*/; do
  name=$(basename "$t")
  ( cd "$t" && bash "$AIQE_ROOT/engine/gate/gate.sh" "$KEY" "$name" ) \
    && SUMMARY+=$'\n'"- ${name}: committed ✅" \
    || SUMMARY+=$'\n'"- ${name}: quarantined ❌ (see reports)"
done
[ "$MODE" = "jira" ] && TRACKER comment "$KEY" "$SUMMARY"
NOTIFY post "$SUMMARY"
python3 engine/lib/run_record.py "$RUN_ID" "$MODE" "$KEY" | TELEM emit_event
