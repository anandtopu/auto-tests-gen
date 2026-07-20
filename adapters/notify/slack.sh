#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Notify port: post <message> | digest <file>
case "$VERB" in
  post)   MSG=${1:-$(cat)}; curl -s -X POST -H 'Content-type: application/json' \
          -d "$(python3 -c "import json,sys;print(json.dumps({'text':sys.argv[1]}))" "$MSG")" \
          "${SLACK_WEBHOOK_URL}" >/dev/null && echo ok ;;
  digest) bash "$0" post "$(cat "$1")" ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
