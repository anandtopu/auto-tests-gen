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
  # OS-assigned free port (parallel gates each boot their own app instance);
  # per-invocation log so concurrent gates never clobber each other's diagnostics
  PORT=$(python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()")
  LOG=$(mktemp "${TMPDIR:-/tmp}/aiqe-env.XXXXXX.log")
  HEALTH=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['test_env'].get('health_path','/'))")
  ( exec env PORT=$PORT node "$APP" ) < /dev/null > "$LOG" 2>&1 &
  PID=$!
  READY=0
  for i in $(seq 1 25); do
    curl -s -m 1 "http://localhost:$PORT$HEALTH" > /dev/null 2>&1 && { READY=1; break; }
    kill -0 "$PID" 2>/dev/null || { echo "APP_START_FAILED"; cat "$LOG"; exit 7; }
    sleep 0.2
  done
  # Never run tests against a half-started app — a timeout is a provisioning
  # failure, not a test failure to blame on the generated specs.
  [ "$READY" = "1" ] || { echo "APP_START_FAILED (not ready after 5s)"; cat "$LOG"; exit 7; }
  export "$VAR=http://localhost:$PORT"
else
  URL=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['test_env']['url'])")
  export "$VAR=$URL"
fi
"$@"
