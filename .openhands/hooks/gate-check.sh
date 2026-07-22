#!/usr/bin/env bash
# OpenHands Stop hook — refuse to let an agent declare a task done if the
# deterministic quality gate would reject its work.
#
# Registered in .openhands/hooks.json on the `stop` event. OpenHands runs stop
# hooks when the agent tries to finish; exit 2 (or {"decision":"deny"}) blocks
# completion and hands the reason back to the agent, which can then fix and retry.
#
# What it does NOT do: commit or push. It runs the gate in check-only mode
# (AIQE_GATE_CHECK_ONLY=1), which performs every check — scope + filename charset,
# born-mapped catalog sidecar, lint, executing the changed specs in a provisioned
# env, secret scan — and stops before writing anything. engine/gate/gate.sh
# remains the only component that ever commits, and the pipeline still runs it for
# real afterwards. This hook just moves that verdict earlier, into the agent loop.
#
# Fail-open by design: if the hook cannot determine an answer (no workspace, no
# python, unreadable repo) it allows completion rather than blocking on its own
# malfunction. The real gate still runs and will reject — nothing gets through
# unchecked, it just gets caught later.
set -uo pipefail        # deliberately not -e: we must always emit a decision

ROOT="${OPENHANDS_PROJECT_DIR:-${AIQE_ROOT:-$PWD}}"
cd "$ROOT" 2>/dev/null || { echo '{"decision":"allow"}'; exit 0; }

allow() { echo '{"decision":"allow"}'; exit 0; }
[ -f engine/gate/gate.sh ] || allow        # not the control repo — nothing to check

shopt -s nullglob
REPOS=(workspace/tests/*/)
[ ${#REPOS[@]} -eq 0 ] && allow            # no generated work in flight

KEY="${KEY:-${AIQE_KEY:-stop-hook}}"
FAILS=()
for t in "${REPOS[@]}"; do
  name=$(basename "$t")
  out=$( cd "$t" && AIQE_ROOT="$ROOT" AIQE_GATE_CHECK_ONLY=1 \
         timeout 300 bash "$ROOT/engine/gate/gate.sh" "$KEY" "$name" 2>&1 )
  rc=$?
  case $rc in
    0) : ;;                                # WOULD_COMMIT or NO_CHANGES
    2) FAILS+=("$name: out-of-scope path or unsafe filename (exit 2)") ;;
    3) FAILS+=("$name: secret/PII pattern in new content (exit 3)") ;;
    4) FAILS+=("$name: a new spec has no catalog sidecar entry — every test must be born-mapped (exit 4)") ;;
    5) FAILS+=("$name: the generated tests FAILED when executed (exit 5)") ;;
    6) FAILS+=("$name: not a standalone test repo (exit 6)") ;;
    124) FAILS+=("$name: gate checks timed out after 300s") ;;
    *) FAILS+=("$name: gate exit $rc") ;;
  esac
  # last few lines carry the actionable detail (which spec, which assertion)
  if [ $rc -ne 0 ]; then
    FAILS+=("    $(printf '%s' "$out" | tail -3 | tr '\n' ' ' | cut -c1-300)")
  fi
done

[ ${#FAILS[@]} -eq 0 ] && allow

# Block, and tell the agent exactly what to fix.
REASON="The AI-QE quality gate would reject this work, so the task is not done yet:
$(printf '%s\n' "${FAILS[@]}")

Fix these in the test repo under workspace/tests/ and finish again. Do not commit or
push — engine/gate/gate.sh is the only component permitted to do that."

if command -v python3 >/dev/null 2>&1; then
  python3 -c 'import json,sys; print(json.dumps({"decision":"deny","reason":sys.argv[1]}))' "$REASON"
else
  printf '{"decision":"deny","reason":%s}\n' "\"$(printf '%s' "$REASON" | tr '\n' ' ' | sed 's/"/\\"/g')\""
fi
exit 2
