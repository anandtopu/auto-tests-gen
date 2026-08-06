#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HELPER="!bash \"${AIQE_ROOT:-$HERE}/adapters/scm/git-credential-aiqe.sh\" github"

clone_with_token() {
  local depth="$1" url="$2" target="$3"
  : "${GITHUB_TOKEN:?GITHUB_TOKEN not set}"
  GIT_TERMINAL_PROMPT=0 \
  GIT_CONFIG_COUNT=1 \
  GIT_CONFIG_KEY_0=credential.helper \
  GIT_CONFIG_VALUE_0="$HELPER" \
    git clone $depth "$url" "$target"
}

# Scm port: clone_ro | clone_rw | changed_files | diff | comment | open_pr | set_status
case "$VERB" in
  changed_files) gh pr view "$2" --repo "org/$1" --json files -q '.files[].path' ;;
  diff)      gh pr diff "$2" --repo "org/$1" ;;
  pr_context) gh pr view "$2" --repo "org/$1" --json headRefName,title,body,commits |
    python3 -c "
import json,sys
d=json.load(sys.stdin); msgs=[]
for c in d.get('commits') or []:
    msg='\n'.join(filter(None,[c.get('messageHeadline',''),c.get('messageBody','')])).strip()
    if msg: msgs.append(msg)
print(json.dumps({'state':'available','source_branch':d.get('headRefName',''),
 'title':d.get('title',''),'description':d.get('body',''),'commit_messages':msgs}))" ;;
  set_status)  # set_status <repo> <sha> <success|failure|pending> <description>
    gh api "repos/org/$1/statuses/$2" -f state="$3" -f context="ai-qe" \
      -f description="$4" >/dev/null && echo ok ;;
  clone_ro)  clone_with_token "--depth 1" "https://github.com/org/$1.git" "$2" ;;
  clone_rw)  clone_with_token "" "https://github.com/org/$1.git" "$2" \
             && git -C "$2" config credential.helper "$HELPER" \
             && git -C "$2" checkout -B "$3" ;;
  comment)   gh pr comment "$2" --repo "org/$1" --body "$3" ;;
  # fetch_file <repo> <path> [ref] — raw file without cloning.
  # Exit 3 = file absent (callers treat that as "no such guidance in this repo").
  # Exit 3 = FILE absent (404 on the file with the REPO confirmed visible).
  # GitHub answers 404 — not 403 — for a private repo the token cannot see, so a
  # bare 404 must not read as "file deleted" (guidance_sync drops cached files
  # on 3). Every other failure — expired token, rate limit, network — exits 1.
  fetch_file) ERR=$(mktemp "${TMPDIR:-/tmp}/aiqe-gh.XXXXXX")
    trap 'rm -f "$ERR"' EXIT
    if gh api "repos/org/$1/contents/$2${3:+?ref=$3}" \
         -H "Accept: application/vnd.github.raw" 2>"$ERR"; then :
    elif grep -q "HTTP 404" "$ERR"; then
      if gh api "repos/org/$1" >/dev/null 2>&1; then echo "NOT_FOUND: $1:$2" >&2; exit 3; fi
      echo "FETCH_FAILED (repo invisible or renamed): $1:$2" >&2; exit 1
    else cat "$ERR" >&2; echo "FETCH_FAILED: $1:$2" >&2; exit 1; fi ;;
  open_pr)   gh pr create --repo "org/$1" --head "$2" --title "$3" --body "$4" ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
