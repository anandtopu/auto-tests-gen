#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Scm port (Bitbucket) — same verbs as github.sh. Uses Atlassian MCP in-run where
# available; REST fallback shown. TODO: fill workspace slug.
BB="https://api.bitbucket.org/2.0/repositories/workspace"
case "$VERB" in
  changed_files) curl -su "x-token-auth:${BITBUCKET_TOKEN}" "$BB/$1/pullrequests/$2/diffstat" \
                 | python3 -c "import json,sys;[print(v['new']['path']) for v in json.load(sys.stdin)['values']]" ;;
  diff)      curl -sLu "x-token-auth:${BITBUCKET_TOKEN}" "$BB/$1/pullrequests/$2/diff" ;;
  set_status)  # set_status <repo> <sha> <success|failure|pending> <description>
    STATE=$(case "$3" in success) echo SUCCESSFUL;; failure) echo FAILED;; *) echo INPROGRESS;; esac)
    curl -sLu "x-token-auth:${BITBUCKET_TOKEN}" -H 'Content-Type: application/json' \
      -d "{\"key\":\"ai-qe\",\"state\":\"$STATE\",\"name\":\"AI QE\",\"description\":\"$4\",\"url\":\"${AIQE_STATUS_URL:-https://ai-qe.invalid}\"}" \
      "$BB/$1/commit/$2/statuses/build" >/dev/null && echo ok ;;
  clone_ro)  git clone --depth 1 "https://x-token-auth:${BITBUCKET_TOKEN}@bitbucket.org/workspace/$1.git" "$2" ;;
  clone_rw)  git clone "https://x-token-auth:${BITBUCKET_TOKEN}@bitbucket.org/workspace/$1.git" "$2" \
             && git -C "$2" checkout -B "$3" ;;
  comment)   curl -su "x-token-auth:${BITBUCKET_TOKEN}" -H 'Content-Type: application/json' \
             -d "{\"content\":{\"raw\":\"$3\"}}" "$BB/$1/pullrequests/$2/comments" >/dev/null ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
