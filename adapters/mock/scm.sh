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

# Windows holds transient handles on freshly-used files (indexer, AV, a just-exited
# git/node), so `rm -rf` on a workspace clone intermittently fails with "Device or
# resource busy". Under `set -e` that killed the ENTIRE pipeline run before any
# phase — no run record, no gate, nothing to diagnose. Retry briefly, then fall
# back to emptying the directory in place; only give up if even that fails.
# Best-effort by contract: clearing is a cleanliness measure, landing the
# checkout is the job. A file we cannot delete (open handle) must never fail the
# clone — the copy below overwrites what matters and the run proceeds.
robust_rm() {
  local target="$1" i
  [ -e "$target" ] || return 0
  for i in 1 2 3 4 5; do
    rm -rf "$target" 2>/dev/null && return 0
    sleep 0.4
  done
  if [ -d "$target" ]; then
    find "$target" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
    if [ -n "$(ls -A "$target" 2>/dev/null)" ]; then
      echo "[mock-scm] note: $target had locked leftovers; copying over them" >&2
    fi
  fi
  return 0
}

# cp -r into an existing dir would nest (demo/x -> target/x); copy CONTENTS.
copy_demo() {
  mkdir -p "$2"
  cp -r "demo/$1/." "$2/"
}
case "$VERB" in
  # No fallback to out/changed.txt: the pipeline redirects INTO that file, so the
  # shell has already truncated it before this adapter runs — a missing fixture
  # must fail loudly, not resolve an empty change list.
  changed_files) cat "eval/benchmark/prs/.changed-$1-$2.txt" 2>/dev/null \
    || { echo "[mock-scm] no changed-files fixture for $1#$2" >&2; exit 1; } ;;
  diff)      cat "eval/benchmark/prs/.diff-$1-$2.txt" 2>/dev/null || true ;;
  set_status) echo "[mock-scm] build status $1@$2 -> $3 ($4)" ;;
  # fetch_file <repo> <path> [ref] — served from the demo estate (stands in for the
  # remote). Exit 3 = absent, matching the real adapters.
  fetch_file) [ -f "demo/$1/$2" ] || { echo "NOT_FOUND: $1:$2" >&2; exit 3; }
              cat "demo/$1/$2" ;;
  clone_ro)  robust_rm "$2"; mkdir -p "$(dirname "$2")"; copy_demo "$1" "$2"; ensure_git "$2" ;;
  clone_rw)  robust_rm "$2"; mkdir -p "$(dirname "$2")"; copy_demo "$1" "$2"; ensure_git "$2"; git -C "$2" checkout -qB "$3" ;;
  comment)   echo "[mock-scm] comment on $1#$2: $3" | tee -a out/mock-comments.log ;;
  open_pr)   echo "[mock-scm] PR on $1 from $2: $3" | tee -a out/mock-comments.log ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
