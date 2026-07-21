#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true
case "$VERB" in
  post)   echo "[mock-slack] ${1:-$(cat)}" | tee -a out/mock-comments.log ;;
  digest) bash "$0" post "$(cat "$1")" ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
