#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Scm port — Bitbucket Server / Data Center ("Stash", self-hosted).
# Same verbs as github.sh/bitbucket.sh; REST API 1.0 (not Cloud's 2.0).
#   STASH_URL      e.g. https://stash.company.com  (no trailing slash)
#   STASH_PROJECT  project key the repos live under, e.g. ENG
#   STASH_TOKEN    HTTP access token (personal or project-scoped), Bearer auth
# Lazy credential check: unknown-verb probes (conformance) run without env vars set
req() {
  S="${STASH_URL:?STASH_URL not set}/rest/api/1.0/projects/${STASH_PROJECT:?STASH_PROJECT not set}"
  AUTH=(-H "Authorization: Bearer ${STASH_TOKEN:?STASH_TOKEN not set}")
  # bash pattern substitution — a token containing sed metachars (#, &, \) must
  # not corrupt the clone URL or the credential
  CLONE_BASE="${STASH_URL/:\/\//:\/\/x-token-auth:${STASH_TOKEN}@}/scm/${STASH_PROJECT}"
  # Corporate CA networks: AIQE_SSL_VERIFY=0 disables certificate verification
  SSL_FLAG=(); [[ "${AIQE_SSL_VERIFY:-1}" == "0" ]] && SSL_FLAG=(-k)
}

case "$VERB" in
  changed_files) req
    # PR diff file list (paged; limit=1000 covers PoC-scale PRs)
    curl -s "${SSL_FLAG[@]}" "${AUTH[@]}" "$S/repos/$1/pull-requests/$2/changes?limit=1000" \
      | python3 -c "import json,sys;[print(v['path']['toString']) for v in json.load(sys.stdin)['values']]" ;;
  clone_ro) req
    git clone --depth 1 "${CLONE_BASE}/$1.git" "$2" ;;
  # fetch_file <repo> <path> [ref] — raw file without cloning (Server raw endpoint).
  # Exit 3 = file absent.
  fetch_file) req
    OUT=$(curl -sf "${SSL_FLAG[@]}" "${AUTH[@]}" "$S/repos/$1/raw/$2${3:+?at=$3}") \
      || { echo "NOT_FOUND: $1:$2" >&2; exit 3; }
    printf '%s' "$OUT" ;;
  clone_rw) req
    git clone "${CLONE_BASE}/$1.git" "$2" \
      && git -C "$2" checkout -B "$3" ;;
  diff) req
    # Server's diff API is JSON; flatten hunks to unified-style text for the phases
    curl -s "${SSL_FLAG[@]}" "${AUTH[@]}" "$S/repos/$1/pull-requests/$2/diff?contextLines=3" | python3 -c "
import json, sys
d = json.load(sys.stdin)
mark = {'ADDED': '+', 'REMOVED': '-', 'CONTEXT': ' '}
for f in d.get('diffs', []):
    src = (f.get('source') or {}).get('toString', '/dev/null')
    dst = (f.get('destination') or {}).get('toString', '/dev/null')
    print(f'--- a/{src}\n+++ b/{dst}')
    for h in f.get('hunks') or []:
        print(f\"@@ -{h['sourceLine']},{h['sourceSpan']} +{h['destinationLine']},{h['destinationSpan']} @@\")
        for seg in h.get('segments', []):
            p = mark.get(seg.get('type'), ' ')
            for ln in seg.get('lines', []):
                print(p + ln.get('line', ''))" ;;
  set_status) req  # set_status <repo> <sha> <success|failure|pending> <description>
    STATE=$(case "$3" in success) echo SUCCESSFUL;; failure) echo FAILED;; *) echo INPROGRESS;; esac)
    curl -s "${SSL_FLAG[@]}" "${AUTH[@]}" -H 'Content-Type: application/json' \
      -d "$(python3 -c "import json,sys;print(json.dumps({'key':'ai-qe','state':sys.argv[1],'name':'AI QE','description':sys.argv[2],'url':sys.argv[3]}))" "$STATE" "$4" "${AIQE_STATUS_URL:-https://ai-qe.invalid}")" \
      "${STASH_URL}/rest/build-status/1.0/commits/$2" >/dev/null && echo ok ;;
  comment) req
    curl -s "${SSL_FLAG[@]}" "${AUTH[@]}" -H 'Content-Type: application/json' \
      -d "$(python3 -c "import json,sys;print(json.dumps({'text':sys.argv[1]}))" "$3")" \
      "$S/repos/$1/pull-requests/$2/comments" >/dev/null && echo ok ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
