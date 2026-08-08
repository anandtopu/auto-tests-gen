#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

run_search() {
  python3 engine/lib/ticket_search.py mock "${1:-'{}'}"
}

case "$VERB" in
  get_item) [ -f "eval/benchmark/tickets/.item-$1.json" ] || exit 3
            cat "eval/benchmark/tickets/.item-$1.json" ;;
  search) run_search "${1:-'{}'}" ;;
  search_release)  # compatibility: the old list response, over structured search
    FILTERS=$(python3 engine/lib/ticket_search.py release "${1:-}") || exit $?
    run_search "$FILTERS" | python3 -c \
      "import json,sys; print(json.dumps(json.load(sys.stdin)['items']))" ;;
  attach)   # attach <KEY> <file> -> out/mock-jira-attachments/
    mkdir -p out/mock-jira-attachments
    cp "$2" "out/mock-jira-attachments/$1-$(basename "$2")"
    echo "[mock-jira] attached to $1: out/mock-jira-attachments/$1-$(basename "$2")" ;;
  comment_capabilities) echo "update_comment=available" ;;
  comment)  RESULT=$(python3 engine/lib/mock_tracker_comments.py post "$1" "$2")
            ID=${RESULT#comment_id=}
            echo "[mock-jira] $1 <- $2 comment_id=$ID" | tee -a out/mock-comments.log ;;
  update_comment) # key id body expected-platform-author
            RESULT=$(python3 engine/lib/mock_tracker_comments.py update \
              "$1" "$2" "$3" "$4")
            ID=${RESULT#comment_id=}
            echo "[mock-jira] $1 updated $ID <- $3 comment_id=$ID" \
              | tee -a out/mock-comments.log ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
