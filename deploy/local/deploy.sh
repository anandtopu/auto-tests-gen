#!/usr/bin/env bash
# Local deployment: build the image, start both services, wait for health, and
# (optionally) seed the demo estate so the dashboard has data on first load.
#
#   ./deploy.sh              build + up + health check
#   ./deploy.sh --seed       also run demo-bootstrap + demo-pr + demo-jira inside
#   ./deploy.sh --down       stop the stack (state volumes are preserved)
set -euo pipefail
cd "$(dirname "$0")"

# Compose engine, auto-detected (mirrors make docker-build's engine detection).
# Order: docker compose — only with a LIVE daemon (a CLI whose daemon is down is
# the trap this machine sat in) — then docker-compose, podman compose,
# podman-compose, and finally podman compose inside the default WSL machine.
# Override explicitly:  COMPOSE="podman compose" ./deploy.sh
if [ -z "${COMPOSE:-}" ]; then
  if docker compose version >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    COMPOSE="docker compose"
  elif command -v docker-compose >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    COMPOSE="docker-compose"                     # v1 talks to the same daemon
  elif podman compose version >/dev/null 2>&1; then
    COMPOSE="podman compose"
  elif command -v podman-compose >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
    COMPOSE="podman-compose"
  elif wsl -d podman-machine-default podman compose version >/dev/null 2>&1; then
    COMPOSE="wsl -d podman-machine-default podman compose"
  else
    echo "ERROR: no compose engine found (docker compose / docker-compose /" >&2
    echo "       podman compose / podman-compose; or set COMPOSE=...)" >&2
    exit 1
  fi
fi
echo "==> compose engine: $COMPOSE"

case "${1:-up}" in
  --down|down) exec $COMPOSE down ;;
esac

echo "==> Building image and starting services (mock mode; no credentials needed)…"
$COMPOSE up -d --build

echo "==> Waiting for the services to become healthy…"
# Use the receiver's unauthenticated /healthz (port 4998) — the dashboard's
# /api/queue requires auth when AIQE_UI_TOKEN is set, so it would return 401.
for i in $(seq 1 40); do
  if curl -fsS -o /dev/null "http://localhost:4998/healthz" 2>/dev/null; then
    echo "    services are up."
    break
  fi
  [ "$i" -eq 40 ] && { echo "    services did not come up in time — check: $COMPOSE logs"; exit 1; }
  sleep 1
done

if [ "${1:-}" = "--seed" ]; then
  echo "==> Seeding the demo estate (bootstrap + Workflow A + B)…"
  $COMPOSE exec -T dashboard bash -lc "make demo-bootstrap && make demo-pr && make demo-jira" \
    || echo "    (seed step reported an issue — the services are still up)"
fi

UI_TOKEN=$(grep -E '^AIQE_UI_TOKEN=' .env 2>/dev/null | cut -d= -f2- | tr -d "'\"" || true)
HOOK_TOKEN=$(grep -E '^AIQE_HOOK_TOKEN=' .env 2>/dev/null | cut -d= -f2- | tr -d "'\"" || true)
MOCK=$(grep -E '^AIQE_MOCK=' .env 2>/dev/null | cut -d= -f2- || echo "1")

cat <<EOF

AI QE platform is running locally:
  Dashboard          http://localhost:4999${UI_TOKEN:+/?token=$UI_TOKEN}
  TaskEvent receiver http://localhost:4998/hooks/taskevent${HOOK_TOKEN:+  (X-AIQE-Token: $HOOK_TOKEN)}

  Logs:   $COMPOSE logs -f
  Stop:   ./deploy.sh --down   (named volumes keep run history / queue / reviews)

$([ "$MOCK" = "0" ] && echo "Real mode (AIQE_MOCK=0): using live adapters." || echo "Mock mode is on (AIQE_MOCK=1). For real runs, set AIQE_MOCK=0 in .env.")
EOF
