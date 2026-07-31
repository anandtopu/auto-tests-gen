#!/usr/bin/env bash
# LLM Runner port — mock shim (multi-LLM story 1.1).
#
# Under AIQE_MOCK=1 the pipeline short-circuits to mock_phase.sh BEFORE
# provider selection, so this adapter exists for conformance parity and for
# anything that resolves the port directly. run_phase here is a deliberate,
# explained refusal — a mock provider silently emitting empty contracts would
# be worse than an error.
set -euo pipefail
VERB=${1:?verb}; shift || true

case "$VERB" in
  run_phase)
    echo "mock provider: phases run via engine/phases/mock_phase.sh under" \
         "AIQE_MOCK=1 (the pipeline short-circuits before provider selection)" >&2
    exit 2
    ;;
  capabilities)
    echo "agentic"
    ;;
  check)
    echo "mock provider (always available)"
    ;;
  tool_policy)
    POLICY=${1:-}
    case "$POLICY" in
      *Write*|*Edit*) echo "writable mock allowedTools=$POLICY" ;;
      *)              echo "readonly mock allowedTools=$POLICY" ;;
    esac
    ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
