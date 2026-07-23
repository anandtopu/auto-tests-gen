#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Scm port — Bitbucket Server / Data Center ("Stash", self-hosted).
# Same verbs as github.sh/bitbucket.sh; REST API 1.0 (not Cloud's 2.0).
#   STASH_URL      e.g. https://stash.company.com  (no trailing slash)
#   STASH_PROJECT  DEFAULT project key, used only for repos that don't declare their
#                  own. A real estate spreads repos across several projects, so each
#                  repo's project + slug is resolved PER REPO from the registry (its
#                  `url` = PROJECT/slug, or an explicit `stash_project` field) by
#                  engine/lib/stash_target.py — STASH_PROJECT is the fallback.
#   STASH_TOKEN    HTTP access token (personal or project-scoped), Bearer auth
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"    # repo root, adapter-relative
# Lazy credential check: unknown-verb probes (conformance) run without env vars set.
# req <repo>: resolve that repo's project + slug, then build the REST/clone bases.
req() {
  local repo="${1:-}"
  # PROJ/SLUG are intentionally GLOBAL (like S/AUTH/CLONE_BASE below): the verb
  # bodies reference $SLUG after req returns, so `local` would unbind them.
  PROJ=""; SLUG=""
  if [ -n "$repo" ]; then
    # Per-repo project (test repos and app repos can live under different projects)
    local line
    line="$(python3 "${AIQE_ROOT:-$HERE}/engine/lib/stash_target.py" "$repo")" \
      || { echo "stash: cannot resolve project for '$repo' (set its url to PROJECT/slug, a stash_project field, or STASH_PROJECT)" >&2; exit 3; }
    PROJ="${line%%$'\t'*}"; SLUG="${line#*$'\t'}"
  fi
  PROJ="${PROJ:-${STASH_PROJECT:?STASH_PROJECT not set}}"
  SLUG="${SLUG:-$repo}"
  S="${STASH_URL:?STASH_URL not set}/rest/api/1.0/projects/${PROJ}"
  AUTH=(-H "Authorization: Bearer ${STASH_TOKEN:?STASH_TOKEN not set}")
  # bash pattern substitution — a token containing sed metachars (#, &, \) must
  # not corrupt the clone URL or the credential
  CLONE_BASE="${STASH_URL/:\/\//:\/\/x-token-auth:${STASH_TOKEN}@}/scm/${PROJ}"
  # Corporate CA networks: AIQE_SSL_VERIFY=0 disables certificate verification.
  # `if` (not `&&`) so req can never return non-zero from a false test — under
  # `set -e` that would abort the caller before it reached git/curl.
  SSL_FLAG=()
  if [[ "${AIQE_SSL_VERIFY:-1}" == "0" ]]; then SSL_FLAG=(-k); fi
}

case "$VERB" in
  changed_files) req "$1"
    # PR diff file list (paged; limit=1000 covers PoC-scale PRs)
    curl -s "${SSL_FLAG[@]}" "${AUTH[@]}" "$S/repos/$SLUG/pull-requests/$2/changes?limit=1000" \
      | python3 -c "import json,sys;[print(v['path']['toString']) for v in json.load(sys.stdin)['values']]" ;;
  clone_ro) req "$1"
    git clone --depth 1 "${CLONE_BASE}/$SLUG.git" "$2" ;;
  # fetch_file <repo> <path> [ref] — raw file without cloning (Server raw endpoint).
  # Exit 3 = file absent.
  fetch_file) req "$1"
    OUT=$(curl -sf "${SSL_FLAG[@]}" "${AUTH[@]}" "$S/repos/$SLUG/raw/$2${3:+?at=$3}") \
      || { echo "NOT_FOUND: $1:$2" >&2; exit 3; }
    printf '%s' "$OUT" ;;
  clone_rw) req "$1"
    git clone "${CLONE_BASE}/$SLUG.git" "$2" \
      && git -C "$2" checkout -B "$3" ;;
  diff) req "$1"
    # Server's diff API is JSON; flatten hunks to unified-style text for the phases
    curl -s "${SSL_FLAG[@]}" "${AUTH[@]}" "$S/repos/$SLUG/pull-requests/$2/diff?contextLines=3" | python3 -c "
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
  set_status) req "$1"  # set_status <repo> <sha> <success|failure|pending> <description>
    STATE=$(case "$3" in success) echo SUCCESSFUL;; failure) echo FAILED;; *) echo INPROGRESS;; esac)
    curl -s "${SSL_FLAG[@]}" "${AUTH[@]}" -H 'Content-Type: application/json' \
      -d "$(python3 -c "import json,sys;print(json.dumps({'key':'ai-qe','state':sys.argv[1],'name':'AI QE','description':sys.argv[2],'url':sys.argv[3]}))" "$STATE" "$4" "${AIQE_STATUS_URL:-https://ai-qe.invalid}")" \
      "${STASH_URL}/rest/build-status/1.0/commits/$2" >/dev/null && echo ok ;;
  comment) req "$1"
    curl -s "${SSL_FLAG[@]}" "${AUTH[@]}" -H 'Content-Type: application/json' \
      -d "$(python3 -c "import json,sys;print(json.dumps({'text':sys.argv[1]}))" "$3")" \
      "$S/repos/$SLUG/pull-requests/$2/comments" >/dev/null && echo ok ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
