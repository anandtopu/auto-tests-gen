#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Telemetry port: emit_event (stdin JSON -> Splunk HEC)
case "$VERB" in
  emit_event) EV=$(cat); curl -s -k "${SPLUNK_HEC_URL}/services/collector/event" \
    -H "Authorization: Splunk ${SPLUNK_HEC_TOKEN}" \
    -d "{\"sourcetype\":\"ai_qe:run\",\"event\":${EV}}" >/dev/null && echo ok ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
