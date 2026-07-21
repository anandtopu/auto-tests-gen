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
  CLONE_BASE="$(echo "${STASH_URL}" | sed "s#://#://x-token-auth:${STASH_TOKEN}@#")/scm/${STASH_PROJECT}"
}

case "$VERB" in
  changed_files) req
    # PR diff file list (paged; limit=1000 covers PoC-scale PRs)
    curl -s "${AUTH[@]}" "$S/repos/$1/pull-requests/$2/changes?limit=1000" \
      | python3 -c "import json,sys;[print(v['path']['toString']) for v in json.load(sys.stdin)['values']]" ;;
  clone_ro) req
    git clone --depth 1 "${CLONE_BASE}/$1.git" "$2" ;;
  clone_rw) req
    git clone "${CLONE_BASE}/$1.git" "$2" \
      && git -C "$2" checkout -B "$3" ;;
  comment) req
    curl -s "${AUTH[@]}" -H 'Content-Type: application/json' \
      -d "{\"text\":\"$3\"}" "$S/repos/$1/pull-requests/$2/comments" >/dev/null && echo ok ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
