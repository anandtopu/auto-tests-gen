#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true

# Scm port: clone_ro | clone_rw | changed_files | diff | comment | open_pr | set_status
case "$VERB" in
  changed_files) gh pr view "$2" --repo "org/$1" --json files -q '.files[].path' ;;
  diff)      gh pr diff "$2" --repo "org/$1" ;;
  set_status)  # set_status <repo> <sha> <success|failure|pending> <description>
    gh api "repos/org/$1/statuses/$2" -f state="$3" -f context="ai-qe" \
      -f description="$4" >/dev/null && echo ok ;;
  clone_ro)  git clone --depth 1 "https://x-access-token:${GITHUB_TOKEN}@github.com/org/$1.git" "$2" ;;
  clone_rw)  git clone "https://x-access-token:${GITHUB_TOKEN}@github.com/org/$1.git" "$2" \
             && git -C "$2" checkout -B "$3" ;;
  comment)   gh pr comment "$2" --repo "org/$1" --body "$3" ;;
  open_pr)   gh pr create --repo "org/$1" --head "$2" --title "$3" --body "$4" ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
