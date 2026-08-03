#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Notify port: post <message> | digest <file>
case "$VERB" in
  post)   MSG=${1:-$(cat)}
          # An unconfigured webhook used to hit `set -u` and abort with
          # "SLACK_WEBHOOK_URL: unbound variable" — a bash internal error where
          # an operator needed "Slack is not configured". Every caller wraps
          # this in `|| true`, so the message vanished and the reason with it.
          # Still non-zero: nothing was delivered, and the Notify port records
          # notify.failed truthfully rather than reporting a send that never
          # happened.
          if [ -z "${SLACK_WEBHOOK_URL-}" ]; then
            echo "NOTIFY_UNCONFIGURED: SLACK_WEBHOOK_URL is not set - nothing was sent." \
                 "Set it in .env / aiqe.properties, or select another channel with" \
                 "NOTIFY_KIND=email|both." >&2
            exit 1
          fi
          curl -s -X POST -H 'Content-type: application/json' \
          -d "$(python3 -c "import json,sys;print(json.dumps({'text':sys.argv[1]}))" "$MSG")" \
          "${SLACK_WEBHOOK_URL}" >/dev/null && echo ok ;;
  digest) bash "$0" post "$(cat "$1")" ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
