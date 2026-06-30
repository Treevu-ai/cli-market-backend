#!/usr/bin/env bash
# Recover Fly.io unmanaged Postgres cluster (cli-market-db).
# Requires: flyctl on PATH, FLY_API_TOKEN (org token or deploy token with access).
#
# Usage:
#   export FLY_API_TOKEN='FlyV1 ...'
#   ./ops/fly_pg_recover.sh
#
# Options (env):
#   PG_APP=cli-market-db          Postgres app name
#   API_APP=cli-market-api        API app to verify after recovery
#   FORCE_RESTART=1               Pass --force to fly postgres restart
#   SKIP_API_VERIFY=1             Skip SSH verification on API app

set -euo pipefail

PG_APP="${PG_APP:-cli-market-db}"
API_APP="${API_APP:-cli-market-api}"
FORCE_RESTART="${FORCE_RESTART:-0}"
SKIP_API_VERIFY="${SKIP_API_VERIFY:-0}"

if ! command -v fly >/dev/null 2>&1; then
  echo "flyctl not found. Install: curl -L https://fly.io/install.sh | sh"
  exit 1
fi

if [[ -z "${FLY_API_TOKEN:-}" ]]; then
  echo "FLY_API_TOKEN is required. fly auth login or export FLY_API_TOKEN='FlyV1 ...'"
  exit 1
fi

section() { echo ""; echo "=== $* ==="; }

section "Postgres cluster status ($PG_APP)"
fly status --app "$PG_APP" || true
fly postgres list 2>/dev/null | grep -E "NAME|$PG_APP" || fly postgres list || true

section "Machines"
MACHINES="$(fly machines list --app "$PG_APP" 2>/dev/null || true)"
echo "$MACHINES"
if [[ -z "$MACHINES" ]] || echo "$MACHINES" | grep -qi "No machines"; then
  echo "WARN: no machines listed — check volumes and scale count next"
fi

section "Volumes"
fly volumes list --app "$PG_APP" || true

section "Health checks"
fly checks list --app "$PG_APP" 2>/dev/null || true

section "Recent logs (last 80 lines)"
fly logs --app "$PG_APP" --no-tail 2>/dev/null | tail -80 || true

restart_machine() {
  local mid="$1"
  echo "Restarting machine $mid on $PG_APP ..."
  fly machine restart "$mid" --app "$PG_APP"
}

section "Recovery: restart each machine"
if echo "$MACHINES" | grep -qE '^[0-9a-f]{16}'; then
  while read -r mid _rest; do
    [[ -n "$mid" ]] || continue
    restart_machine "$mid" || echo "WARN: restart failed for $mid"
  done < <(echo "$MACHINES" | awk '/^[0-9a-f]{16}/ {print $1}')
else
  echo "No machine IDs found — trying fly postgres restart"
  if [[ "$FORCE_RESTART" == "1" ]]; then
    fly postgres restart --app "$PG_APP" --force --skip-health-checks || true
  else
    fly postgres restart --app "$PG_APP" || true
  fi
fi

section "Wait for leader election"
for i in 1 2 3 4 5 6; do
  if fly postgres connect --app "$PG_APP" -c "SELECT 1" >/dev/null 2>&1; then
    echo "Postgres accepting connections (attempt $i)"
    break
  fi
  echo "Still waiting... ($i/6)"
  sleep 15
done

section "Post-recovery status"
fly status --app "$PG_APP" || true
fly checks list --app "$PG_APP" 2>/dev/null || true

if [[ "$SKIP_API_VERIFY" != "1" ]]; then
  section "Verify API app sees PostgreSQL ($API_APP)"
  fly ssh console --app "$API_APP" -C "python3 -c \"
from market_core import USE_PG, DATABASE_URL, recover_pg_if_needed
recover_pg_if_needed()
print('USE_PG=', USE_PG)
print('DATABASE_URL set=', bool(DATABASE_URL))
\"" || echo "WARN: SSH verify failed — restart API: fly apps restart $API_APP"
fi

section "Smoke: /health/db"
curl -fsS "https://${API_APP}.fly.dev/health/db" | head -c 500 || true
echo ""

echo ""
echo "Done. If USE_PG is still False, restart the API so ensure_db_initialized() reconnects:"
echo "  fly apps restart --app $API_APP"
echo "Then test login:"
echo "  curl -X POST https://${API_APP}.fly.dev/auth/login -H 'Content-Type: application/json' -d '{\"username\":\"admin\",\"password\":\"<MARKET_ADMIN_PASSWORD>\"}'"
