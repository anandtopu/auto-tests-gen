#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Scm port (Bitbucket) — same verbs as github.sh. Uses Atlassian MCP in-run where
# available; REST fallback shown. TODO: fill workspace slug.
BB="https://api.bitbucket.org/2.0/repositories/workspace"
HELPER="!bash \"${AIQE_ROOT:-$HERE}/adapters/scm/git-credential-aiqe.sh\" bitbucket"

clone_with_token() {
  local depth="$1" url="$2" target="$3"
  : "${BITBUCKET_TOKEN:?BITBUCKET_TOKEN not set}"
  GIT_TERMINAL_PROMPT=0 \
  GIT_CONFIG_COUNT=1 \
  GIT_CONFIG_KEY_0=credential.helper \
  GIT_CONFIG_VALUE_0="$HELPER" \
    git clone $depth "$url" "$target"
}

case "$VERB" in
  changed_files) curl -su "x-token-auth:${BITBUCKET_TOKEN}" "$BB/$1/pullrequests/$2/diffstat" \
                 | python3 -c "import json,sys;[print(v['new']['path']) for v in json.load(sys.stdin)['values']]" ;;
  diff)      curl -sLu "x-token-auth:${BITBUCKET_TOKEN}" "$BB/$1/pullrequests/$2/diff" ;;
  set_status)  # set_status <repo> <sha> <success|failure|pending> <description>
    STATE=$(case "$3" in success) echo SUCCESSFUL;; failure) echo FAILED;; *) echo INPROGRESS;; esac)
    curl -sL --fail-with-body -u "x-token-auth:${BITBUCKET_TOKEN}" -H 'Content-Type: application/json' \
      -d "$(python3 -c "import json,sys;print(json.dumps({'key':'ai-qe','state':sys.argv[1],'name':'AI QE','description':sys.argv[2],'url':sys.argv[3]}))" "$STATE" "$4" "${AIQE_STATUS_URL:-https://ai-qe.invalid}")" \
      "$BB/$1/commit/$2/statuses/build" >/dev/null && echo ok ;;
  clone_ro)  clone_with_token "--depth 1" "https://bitbucket.org/workspace/$1.git" "$2" ;;
  clone_rw)  clone_with_token "" "https://bitbucket.org/workspace/$1.git" "$2" \
             && git -C "$2" config credential.helper "$HELPER" \
             && git -C "$2" checkout -B "$3" ;;
  comment)   curl -s --fail-with-body -u "x-token-auth:${BITBUCKET_TOKEN}" -H 'Content-Type: application/json' \
             -d "$(python3 -c "import json,sys;print(json.dumps({'content':{'raw':sys.argv[1]}}))" "$3")" \
             "$BB/$1/pullrequests/$2/comments" >/dev/null ;;
  # fetch_file <repo> <path> [ref] — raw file from the repo without cloning.
  # Exit 3 = file absent (callers treat that as "this repo has no such guidance").
  fetch_file)
    # Exit 3 = FILE absent (404 with the repo confirmed reachable). Everything
    # else exits 1: guidance_sync treats 3 as "remote deleted it" and drops the
    # cached copy — an expired token, an outage, or a repo the token can no
    # longer see (Cloud answers 404, not 403, for invisible repos) must never
    # read as absence.
    REF=${3:-HEAD}
    BODY=$(mktemp "${TMPDIR:-/tmp}/aiqe-bb.XXXXXX")
    trap 'rm -f "$BODY"' EXIT
    HTTP=$(curl -sL -o "$BODY" -w '%{http_code}' -u "x-token-auth:${BITBUCKET_TOKEN}" \
          "$BB/$1/src/${REF}/$2") || { echo "FETCH_FAILED (transport): $1:$2" >&2; exit 1; }
    if [ "$HTTP" = "404" ]; then
      REPO_HTTP=$(curl -sL -o /dev/null -w '%{http_code}' -u "x-token-auth:${BITBUCKET_TOKEN}" \
                  "$BB/$1") || REPO_HTTP=000
      if [ "$REPO_HTTP" = "200" ]; then echo "NOT_FOUND: $1:$2" >&2; exit 3; fi
      echo "FETCH_FAILED (repo unreachable, HTTP $REPO_HTTP): $1:$2" >&2; exit 1
    fi
    if [ "$HTTP" != "200" ]; then echo "FETCH_FAILED (HTTP $HTTP): $1:$2" >&2; exit 1; fi
    cat "$BODY" ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
