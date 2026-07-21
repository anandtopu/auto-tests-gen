#!/usr/bin/env bash
# G5: scoped environment provisioning — start app-under-test, run the given command
# with the base-URL env var exported, then ALWAYS tear down (trap). Usage:
#   bin/with-env.sh <test_repo_dir> -- <command...>
# mode=compose -> hermetic app per invocation (demo: node server; real: docker compose)
# mode=shared  -> no process started; just exports the shared env URL.
set -euo pipefail
TREPO_DIR=${1:?path to test repo}; shift
[ "${1:-}" = "--" ] && shift
CFG="$TREPO_DIR/.ai-qe/config.yaml"
MODE=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['test_env']['mode'])")
VAR=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['test_env']['base_url_env'])")
PID=""
cleanup() { [ -n "$PID" ] && kill "$PID" 2>/dev/null || true; wait "$PID" 2>/dev/null || true; }
trap cleanup EXIT

if [ "$MODE" = "compose" ]; then
  APP_REPO=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['test_env']['app_repo'])")
  APP_ENTRY=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['test_env']['app_entry'])")
  ROOT="${AIQE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
  APP=""
  for base in "$ROOT/workspace/src" "$ROOT/demo"; do
    [ -f "$base/$APP_REPO/$APP_ENTRY" ] && APP="$base/$APP_REPO/$APP_ENTRY" && break
  done
  [ -z "$APP" ] && { echo "APP_REPO_NOT_FOUND: $APP_REPO"; exit 8; }
  PORT=$(( 4600 + RANDOM % 200 ))
  ( exec env PORT=$PORT node "$APP" ) < /dev/null > /tmp/aiqe-env.log 2>&1 &
  PID=$!
  for i in $(seq 1 25); do
    curl -s -m 1 "http://localhost:$PORT/v1/orders/1" > /dev/null 2>&1 && break
    kill -0 "$PID" 2>/dev/null || { echo "APP_START_FAILED"; cat /tmp/aiqe-env.log; exit 7; }
    sleep 0.2
  done
  export "$VAR=http://localhost:$PORT"
else
  URL=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['test_env']['url'])")
  export "$VAR=$URL"
fi
"$@"
