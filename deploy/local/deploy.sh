#!/usr/bin/env bash
# Local deployment: build the image, start both services, wait for health, and
# (optionally) seed the demo estate so the dashboard has data on first load.
#
#   ./deploy.sh              build + up + health check
#   ./deploy.sh --seed       also run demo-bootstrap + demo-pr + demo-jira inside
#   ./deploy.sh --down       stop the stack (state volumes are preserved)
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE="docker-compose"

case "${1:-up}" in
  --down|down) exec $COMPOSE down ;;
esac

echo "==> Building image and starting services (mock mode; no credentials needed)…"
$COMPOSE up -d --build

echo "==> Waiting for the dashboard to become healthy…"
for i in $(seq 1 40); do
  if curl -fsS -o /dev/null "http://localhost:4999/api/queue" 2>/dev/null; then
    echo "    dashboard is up."
    break
  fi
  [ "$i" -eq 40 ] && { echo "    dashboard did not come up in time — check: $COMPOSE logs dashboard"; exit 1; }
  sleep 1
done

if [ "${1:-}" = "--seed" ]; then
  echo "==> Seeding the demo estate (bootstrap + Workflow A + B)…"
  $COMPOSE exec -T dashboard bash -lc "make demo-bootstrap && make demo-pr && make demo-jira" \
    || echo "    (seed step reported an issue — the services are still up)"
fi

cat <<EOF

AI QE platform is running locally:
  Dashboard          http://localhost:4999
  TaskEvent receiver http://localhost:4998/hooks/taskevent  (X-AIQE-Token: change-me)

  Logs:   $COMPOSE logs -f
  Stop:   ./deploy.sh --down   (named volumes keep run history / queue / reviews)

Mock mode is on. For real runs, see docs/deployment.md → "Going real".
EOF
