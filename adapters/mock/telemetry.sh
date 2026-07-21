#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true
case "$VERB" in
  emit_event) mkdir -p out; cat >> out/telemetry.jsonl; echo "[mock-splunk] event recorded" ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
