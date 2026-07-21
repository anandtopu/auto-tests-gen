#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Knowledge port mock — same verbs as adapters/knowledge/confluence.sh.
case "$VERB" in
  get_linked_docs) echo "## Linked PRD (mock): discounts must be 1-90%" ;;
  publish_doc)  # publish_doc <space> <title> <body.html> -> out/mock-confluence/
    SPACE=$1; TITLE=$2; FILE=$3
    SAFE=$(printf '%s' "$TITLE" | tr -c 'A-Za-z0-9._-' '-')
    mkdir -p out/mock-confluence
    cp "$FILE" "out/mock-confluence/${SAFE}.html"
    echo "[mock-confluence] published '${TITLE}' to space ${SPACE}: out/mock-confluence/${SAFE}.html" ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
