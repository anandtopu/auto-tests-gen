#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Notify port (email channel): post <message> | digest <file>
# The first line of the message becomes the subject; the rest is the body. Recipients
# come from SMTP_TO (or --to on the library). With no SMTP_HOST / AIQE_MOCK=1 the
# library writes the email to out/mock-email/ instead of sending it.
send_msg() {
  local msg="$1" subject body
  subject=$(printf '%s' "$msg" | head -1)
  body="$msg"
  python3 "$ROOT/engine/lib/email_notify.py" send "$subject" "$body"
}

case "$VERB" in
  post)   send_msg "${1:-$(cat)}" ;;
  digest) send_msg "$(cat "$1")" ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
