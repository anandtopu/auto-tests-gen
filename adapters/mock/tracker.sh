#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true
case "$VERB" in
  get_item) cat "eval/benchmark/tickets/.item-$1.json" ;;
  comment)  echo "[mock-jira] $1 <- $2" | tee -a out/mock-comments.log ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
