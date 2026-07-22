#!/usr/bin/env bash
# OpenHands live smoke test (REVIEW.md open item: Path-1 live wiring).
# Validates the real integration chain stage by stage, with credentials from .env:
#
#   bash bin/smoke-openhands.sh          # run every check that has credentials
#   bash bin/smoke-openhands.sh --dry    # plumbing only, no network calls
#   AIQE_SMOKE_TRIGGER=1 bash bin/smoke-openhands.sh   # ALSO start a real
#       OpenHands conversation (costs money, runs the pipeline for real)
#
# Required .env for a full pass:
#   OPENHANDS_URL, OPENHANDS_API_KEY          Agent Server (enterprise/cloud)
#   ANTHROPIC_API_KEY                         LLM phases inside the sandbox
#   ATLASSIAN_MCP_TOKEN                       Jira/Confluence
#   GITHUB_TOKEN or BITBUCKET_TOKEN or STASH_* (match SCM_KIND)
#   AIQE_SMOKE_TICKET (e.g. PROJ-123)         a real, readable ticket
#   AIQE_SMOKE_REPO + AIQE_SMOKE_PR           a real, open PR to read
set -u
cd "$(dirname "$0")/.."
source .env 2>/dev/null || true
DRY=0; [ "${1:-}" = "--dry" ] && DRY=1

PASS=0; FAIL=0; SKIP=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
skip() { echo "  [skip] $1"; SKIP=$((SKIP+1)); }

echo "== OpenHands live smoke test =="
echo "   mode: $([ $DRY -eq 1 ] && echo dry-run || echo live)   SCM_KIND=${SCM_KIND:-github}"
echo

# --- Stage 1: credentials present -------------------------------------------------
echo "Stage 1: credentials"
need() { if [ -n "${!1:-}" ]; then ok "$1 set"; else bad "$1 MISSING (add to .env)"; fi }
need OPENHANDS_URL
need OPENHANDS_API_KEY
need ANTHROPIC_API_KEY
need ATLASSIAN_MCP_TOKEN
case "${SCM_KIND:-github}" in
  github)    need GITHUB_TOKEN ;;
  bitbucket) need BITBUCKET_TOKEN ;;
  stash)     need STASH_URL; need STASH_PROJECT; need STASH_TOKEN ;;
esac
if [ -n "${AIQE_SMOKE_TICKET:-}" ]; then ok "AIQE_SMOKE_TICKET=${AIQE_SMOKE_TICKET}"; \
else skip "AIQE_SMOKE_TICKET unset — tracker read stage will be skipped"; fi
if [ -n "${AIQE_SMOKE_REPO:-}" ] && [ -n "${AIQE_SMOKE_PR:-}" ]; then \
  ok "AIQE_SMOKE_REPO/PR=${AIQE_SMOKE_REPO}#${AIQE_SMOKE_PR}"; \
else skip "AIQE_SMOKE_REPO/AIQE_SMOKE_PR unset — SCM read stage will be skipped"; fi

# --- Stage 2: OpenHands Agent Server reachable ------------------------------------
echo "Stage 2: OpenHands Agent Server"
if [ $DRY -eq 1 ] || [ -z "${OPENHANDS_URL:-}" ]; then
  skip "connectivity (dry-run or no URL)"
else
  HP="${OPENHANDS_HEALTH_PATH:-/health}"
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 10 \
    -H "Authorization: Bearer ${OPENHANDS_API_KEY:-}" "${OPENHANDS_URL%/}${HP}" || echo 000)
  case "$CODE" in
    2*|401|403) ok "server responds at ${OPENHANDS_URL%/}${HP} (HTTP $CODE)" ;;
    *) bad "no response from ${OPENHANDS_URL%/}${HP} (HTTP $CODE) — check URL/network/VPN" ;;
  esac
fi

# --- Stage 3: sandbox image -------------------------------------------------------
echo "Stage 3: sandbox image"
IMG="${AIQE_SANDBOX_IMAGE:-ai-qe-sandbox:latest}"
if [ $DRY -eq 1 ]; then
  skip "image check (dry-run)"
elif ! command -v docker >/dev/null 2>&1; then
  skip "docker not on PATH — verify '$IMG' exists in the runtime's registry instead"
elif docker image inspect "$IMG" >/dev/null 2>&1; then
  ok "docker image '$IMG' present"
else
  bad "docker image '$IMG' not found — build: docker build -t $IMG sandbox/"
fi

# --- Stage 4: Tracker (Jira) live read --------------------------------------------
echo "Stage 4: Tracker adapter (Jira read)"
if [ $DRY -eq 1 ] || [ -z "${AIQE_SMOKE_TICKET:-}" ] || [ -z "${ATLASSIAN_MCP_TOKEN:-}" ]; then
  skip "jira get_item (dry-run or missing ticket/token)"
elif OUT=$(bash adapters/tracker/jira.sh get_item "$AIQE_SMOKE_TICKET" 2>&1) \
    && echo "$OUT" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
  ok "read $AIQE_SMOKE_TICKET (summary, components, fixVersions)"
else
  bad "jira get_item failed: $(echo "$OUT" | head -1)"
fi

# --- Stage 5: SCM live read -------------------------------------------------------
echo "Stage 5: SCM adapter (${SCM_KIND:-github} PR read)"
if [ $DRY -eq 1 ] || [ -z "${AIQE_SMOKE_REPO:-}" ]; then
  skip "changed_files/diff (dry-run or no smoke PR configured)"
else
  ADAPTER=$(python3 -c "import yaml;print(yaml.safe_load(open('registry/org-config.yaml'))['adapters']['scm']['${SCM_KIND:-github}'])")
  if FILES=$(bash "$ADAPTER" changed_files "$AIQE_SMOKE_REPO" "$AIQE_SMOKE_PR" 2>&1) \
      && [ -n "$FILES" ]; then
    ok "changed_files: $(echo "$FILES" | wc -l | tr -d ' ') file(s)"
  else
    bad "changed_files failed: $(echo "$FILES" | head -1)"
  fi
  if DIFF=$(bash "$ADAPTER" diff "$AIQE_SMOKE_REPO" "$AIQE_SMOKE_PR" 2>&1) \
      && [ -n "$DIFF" ]; then
    ok "diff: $(echo "$DIFF" | wc -l | tr -d ' ') line(s) of patch"
  else
    bad "diff failed: $(echo "$DIFF" | head -1)"
  fi
fi

# --- Stage 6: Knowledge (Confluence) ----------------------------------------------
echo "Stage 6: Knowledge adapter (Confluence, non-fatal)"
if [ $DRY -eq 1 ] || [ -z "${AIQE_SMOKE_TICKET:-}" ] || [ -z "${CONFLUENCE_URL:-}" ]; then
  skip "linked-doc fetch (dry-run or CONFLUENCE_URL/ticket unset)"
else
  bash adapters/tracker/jira.sh get_item "$AIQE_SMOKE_TICKET" > out/smoke-ticket.json 2>/dev/null
  if bash adapters/knowledge/confluence.sh get_linked_docs out/smoke-ticket.json \
      > out/smoke-confluence.md 2>&1; then
    ok "get_linked_docs ran ($(wc -c < out/smoke-confluence.md | tr -d ' ') bytes of context)"
  else
    skip "get_linked_docs errored — non-fatal (ticket may have no Confluence links)"
  fi
fi

# --- Stage 7: microagent + trigger config sanity ----------------------------------
echo "Stage 7: trigger configuration"
grep -q "ai-tests" triggers/openhands/microagents/ai-qe.md \
  && ok "microagent triggers include ai-tests/ai-test-gen" \
  || bad "microagent triggers missing in triggers/openhands/microagents/ai-qe.md"
python3 -c "import json;json.load(open('triggers/task-event-schema.json'))" 2>/dev/null \
  && ok "TaskEvent schema parses" || bad "task-event-schema.json invalid"

# --- Stage 8: start a REAL conversation (opt-in: costs money) ---------------------
# API shape differs by deployment:
#   self-hosted Agent Server : POST /api/conversations           (default below)
#   OpenHands Cloud          : POST /api/v1/app-conversations    (the V0 /api/conversations
#                              endpoint was slated for removal in 2026 — set
#                              OPENHANDS_CONVERSATIONS_PATH=/api/v1/app-conversations)
# The request body differs too; Cloud takes initial_message.content[] + selected_repository.
echo "Stage 8: live conversation trigger"
if [ "${AIQE_SMOKE_TRIGGER:-0}" != "1" ]; then
  skip "not started (set AIQE_SMOKE_TRIGGER=1 to POST a real conversation)"
  echo "         manual equivalent:"
  echo "         curl -X POST '\${OPENHANDS_URL}/api/conversations' \\"
  echo "           -H 'Authorization: Bearer \${OPENHANDS_API_KEY}' -H 'Content-Type: application/json' \\"
  echo "           -d '{\"initial_user_msg\":\"Run the AI-QE pipeline: bash engine/pipeline.sh jira ${AIQE_SMOKE_TICKET:-PROJ-123}\",\"repository\":\"'\${AIQE_CONTROL_REPO:-org/ai-qe-control}'\"}'"
elif [ $DRY -eq 1 ]; then
  skip "trigger requested but --dry given"
else
  RESP=$(curl -s -m 30 -X POST "${OPENHANDS_URL%/}${OPENHANDS_CONVERSATIONS_PATH:-/api/conversations}" \
    -H "Authorization: Bearer ${OPENHANDS_API_KEY}" -H 'Content-Type: application/json' \
    -d "{\"initial_user_msg\":\"Run the AI-QE pipeline: bash engine/pipeline.sh jira ${AIQE_SMOKE_TICKET:-PROJ-123}\",\"repository\":\"${AIQE_CONTROL_REPO:-org/ai-qe-control}\"}" || echo '{}')
  CID=$(echo "$RESP" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('conversation_id') or d.get('id') or '')" 2>/dev/null || echo "")
  if [ -n "$CID" ]; then
    ok "conversation started: $CID — watch it in the OpenHands UI, then check 'make status' here"
  else
    bad "conversation POST failed: $(echo "$RESP" | head -c 200)"
    echo "         (endpoint shape varies by deployment — Cloud uses"
    echo "          OPENHANDS_CONVERSATIONS_PATH=/api/v1/app-conversations with a"
    echo "          different body; self-hosted Agent Server uses /api/conversations)"
  fi
fi

# --- summary ----------------------------------------------------------------------
echo
echo "== summary: $PASS passed, $FAIL failed, $SKIP skipped =="
if [ $FAIL -gt 0 ]; then
  echo "   fix the [FAIL] lines above, then re-run: make smoke-openhands"
  exit 1
fi
[ $SKIP -gt 0 ] && echo "   skipped stages need credentials/config — see the header of this script"
echo "   after a green Stage 8: verify the PR/ticket comment, 'make status', and the gate commit"
exit 0
