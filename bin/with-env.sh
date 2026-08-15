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
# Every test_env read goes through this. They used to be six bare
# `['test_env']['x']` one-liners, so a missing or misspelt key raised a Python
# KeyError and `set -e` exited 1 with a traceback and NO marker -- and gate.sh
# classifies by MARKER, so it fell through to TESTS_FAILED. MEASURED with
# `mode` misspelt: KeyError, exit 1, zero markers, and the test command never
# ran. That is exactly what the comment beside the gate's grep exists to
# prevent: "the app under test never started" is not "the generated tests
# failed", and reporting it as the latter sends a human to debug tests that
# were never executed. ENV_CONFIG_INVALID is a third way the environment never
# comes up, alongside APP_REPO_NOT_FOUND and APP_START_FAILED.
# Defaults live in PYTHON, never in an argument. Passing `/` as argv from Git
# Bash is rewritten by MSYS path mangling into the Git install root, so
# `_env_cfg health_path /` resolved HEALTH to `C:/Program Files/Git/` and the
# readiness probe hit http://localhost:PORT/C:/Program Files/Git/ forever --
# the app came up and the gate reported APP_START_FAILED. Measured: HEAD's
# `.get('health_path','/')` was immune because the default never crossed the
# shell->exe boundary. Same family as the POSIX-path-to-native-python3 trap
# already recorded in CLAUDE.md.
_env_cfg() {
  python3 - "$CFG" "$1" <<'PYEOF'
import sys, yaml
cfg, key = sys.argv[1], sys.argv[2]
OPTIONAL = {"health_path": "/"}          # key -> default, kept out of argv
try:
    doc = yaml.safe_load(open(cfg, encoding="utf-8")) or {}
except FileNotFoundError:
    sys.exit(f"ENV_CONFIG_INVALID: {cfg} does not exist, so the environment "
             f"for the tests cannot be provisioned.")
except Exception as exc:
    sys.exit(f"ENV_CONFIG_INVALID: {cfg} is not valid YAML ({exc}).")
env = doc.get("test_env") if isinstance(doc, dict) else None
if not isinstance(env, dict):
    sys.exit(f"ENV_CONFIG_INVALID: {cfg} has no `test_env:` section, so there "
             f"is nothing to provision. Add test_env.mode (compose|shared) "
             f"and test_env.base_url_env.")
if key in env and isinstance(env[key], (str, int, float)) and str(env[key]).strip():
    print(env[key])
    sys.exit(0)
if key in OPTIONAL:
    print(OPTIONAL[key])
    sys.exit(0)
sys.exit(f"ENV_CONFIG_INVALID: {cfg} declares no `test_env.{key}`. It has: "
         f"{sorted(env)} - check the spelling. The app under test cannot be "
         f"provisioned, so the tests would never run.")
PYEOF
}
MODE=$(_env_cfg mode) || exit 9
VAR=$(_env_cfg base_url_env) || exit 9
PID=""; LOG=""
# Teardown also removes the per-invocation app log — the failure paths below cat it
# to stdout first, so keeping the temp file would only leak one per gate invocation.
cleanup() {
  [ -n "$PID" ] && kill "$PID" 2>/dev/null || true
  wait "$PID" 2>/dev/null || true
  [ -n "$LOG" ] && rm -f "$LOG" 2>/dev/null || true
}
trap cleanup EXIT

if [ "$MODE" = "compose" ]; then
  APP_REPO=$(_env_cfg app_repo) || exit 9
  APP_ENTRY=$(_env_cfg app_entry) || exit 9
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
  HEALTH=$(_env_cfg health_path) || exit 9
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
  URL=$(_env_cfg url) || exit 9
  export "$VAR=$URL"
fi
"$@"
