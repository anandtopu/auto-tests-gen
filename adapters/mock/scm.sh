#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true
# Scm port against the in-repo demo estate
# Demo dirs carry no .git (nested repos can't be committed to the scaffold), so every
# clone is git-initialized with a baseline commit — without this, git commands inside
# the workspace copy escape to the scaffold's own repository.
ensure_git() {
  [ -d "$1/.git" ] && return 0
  git -C "$1" init -q
  git -C "$1" -c user.email=demo@ai-qe.local -c user.name=ai-qe-demo add -A
  git -C "$1" -c user.email=demo@ai-qe.local -c user.name=ai-qe-demo commit -qm "baseline import (demo estate)"
}
case "$VERB" in
  changed_files) cat "eval/benchmark/prs/.changed-$1-$2.txt" 2>/dev/null || cat out/changed.txt ;;
  diff)      cat "eval/benchmark/prs/.diff-$1-$2.txt" 2>/dev/null || true ;;
  set_status) echo "[mock-scm] build status $1@$2 -> $3 ($4)" ;;
  clone_ro)  rm -rf "$2"; mkdir -p "$(dirname "$2")"; cp -r "demo/$1" "$2"; ensure_git "$2" ;;
  clone_rw)  rm -rf "$2"; mkdir -p "$(dirname "$2")"; cp -r "demo/$1" "$2"; ensure_git "$2"; git -C "$2" checkout -qB "$3" ;;
  comment)   echo "[mock-scm] comment on $1#$2: $3" | tee -a out/mock-comments.log ;;
  open_pr)   echo "[mock-scm] PR on $1 from $2: $3" | tee -a out/mock-comments.log ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
