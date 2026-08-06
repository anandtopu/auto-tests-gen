#!/usr/bin/env bash
set -euo pipefail

# Git invokes shell credential helpers as: <helper> <operation>.  The provider
# is a non-secret argument persisted in writable clones; tokens remain only in
# the process environment and are returned to Git over stdin/stdout.
PROVIDER=${1:?provider}; OP=${2:-get}
[ "$OP" = "get" ] || exit 0

protocol="" host=""
while IFS='=' read -r key value; do
  value=${value%$'\r'}
  case "$key" in
    protocol) protocol=$value ;;
    host) host=$value ;;
  esac
done
[ "$protocol" = "https" ] || exit 0

case "$PROVIDER" in
  github)
    [ "$host" = "github.com" ] || exit 0
    printf 'username=x-access-token\npassword=%s\n' "${GITHUB_TOKEN:?GITHUB_TOKEN not set}"
    ;;
  bitbucket)
    [ "$host" = "bitbucket.org" ] || exit 0
    printf 'username=x-token-auth\npassword=%s\n' "${BITBUCKET_TOKEN:?BITBUCKET_TOKEN not set}"
    ;;
  stash)
    expected=$(python3 -c "import sys,urllib.parse; print(urllib.parse.urlparse(sys.argv[1]).netloc)" \
      "${STASH_URL:?STASH_URL not set}")
    [ -n "$expected" ] && [ "$host" = "$expected" ] || exit 0
    printf 'username=x-token-auth\npassword=%s\n' "${STASH_TOKEN:?STASH_TOKEN not set}"
    ;;
  *) exit 64 ;;
esac
