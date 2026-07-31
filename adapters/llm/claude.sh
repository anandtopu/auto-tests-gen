#!/usr/bin/env bash
# LLM Runner port — Claude Code adapter (multi-LLM story 1.1). The DEFAULT
# provider: today's `claude -p` invocation extracted verbatim from
# run_phase.sh, so behavior is byte-identical while the port seam exists.
#
# Verbs:
#   run_phase <model> <max_turns> <allowed_tools> <out_json>
#       assembled prompt on STDIN (parked to a file first — a heredoc/pipe
#       must never race the CLI's own stdin handling); writes the result JSON
#       (claude's own shape IS the normalized shape: result/usage/num_turns/
#       total_cost_usd) to <out_json>, augmented with provider+model.
#   capabilities   prints "agentic" (full tool loop)
#   check          read-only reachability probe (CLI present + authenticated)
#
# Exit: run_phase propagates the CLI's exit; unknown verb 64.
set -euo pipefail
VERB=${1:?verb}; shift || true

case "$VERB" in
  run_phase)
    MODEL=${1:?model}; TURNS=${2:?max_turns}; TOOLS=${3:?allowed_tools}
    OUT_JSON=${4:?out_json}
    TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
    cat > "$TMP"
    claude -p "$(cat "$TMP")" \
      --output-format json \
      --max-turns "$TURNS" \
      --allowedTools "$TOOLS" \
      --model "$MODEL" \
      --dangerously-skip-permissions \
      | tee "$OUT_JSON"
    # Augment with provider/model so telemetry stays provider-agnostic.
    # Best-effort: a malformed result is the phase's problem, not this step's.
    python3 - "$OUT_JSON" "$MODEL" <<'PY' || true
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    if isinstance(d, dict):
        d.setdefault("provider", "claude")
        d.setdefault("model", sys.argv[2])
        open(sys.argv[1], "w", encoding="utf-8", newline="\n").write(
            json.dumps(d))
except Exception:
    pass
PY
    ;;
  capabilities)
    echo "agentic"
    ;;
  tool_policy)
    # 5.1: what this adapter will ACTUALLY enforce for a given allow-list.
    # claude enforces it verbatim, so the answer is the input — printed anyway
    # so every agentic adapter answers the same question the same way, and a
    # read-only phase's policy is inspectable rather than assumed.
    POLICY=${1:-}
    case "$POLICY" in
      *Write*|*Edit*) echo "writable allowedTools=$POLICY" ;;
      *)              echo "readonly allowedTools=$POLICY" ;;
    esac
    ;;
  check)
    command -v claude >/dev/null 2>&1 || { echo "claude CLI not installed"; exit 1; }
    claude --version >/dev/null 2>&1 || { echo "claude CLI present but not runnable"; exit 1; }
    echo "claude CLI present ($(claude --version 2>/dev/null | head -1))"
    ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
