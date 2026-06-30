# Fly.io Postgres recovery — cli-market-db

When `fly postgres list` shows **no active leader** for `cli-market-db`, the API (`cli-market-api`) falls back to ephemeral SQLite at `/data/market.db`. Users/auth do not persist across restarts → `POST /auth/login` returns 401.

## Prerequisites

```bash
# Install flyctl (Linux/macOS)
curl -L https://fly.io/install.sh | sh
export PATH="$HOME/.fly/bin:$PATH"

# Org access token (Dashboard → Account → Access Tokens)
export FLY_API_TOKEN='FlyV1 ...'
```

## Quick recovery (run in order)

```bash
cd cli-market-backend

# 1. Diagnose
fly status --app cli-market-db
fly machines list --app cli-market-db
fly volumes list --app cli-market-db
fly logs --app cli-market-db --no-tail | tail -100

# 2. Restart each DB machine (most common fix for "no active leader")
fly machines list --app cli-market-db --json | jq -r '.[].id' | while read id; do
  fly machine restart "$id" --app cli-market-db
done

# 3. If step 2 fails — force cluster restart
fly postgres restart --app cli-market-db --force --skip-health-checks

# 4. Confirm Postgres accepts connections
fly postgres connect --app cli-market-db -c "SELECT version();"

# 5. Restart API so it reconnects to PG (recover_pg_if_needed may also work within ~30s)
fly apps restart --app cli-market-api

# 6. Verify USE_PG on the running machine
fly ssh console --app cli-market-api -C "python3 -c 'from market_core import USE_PG; print(USE_PG)'"
# Expected: USE_PG= True

# 7. Smoke endpoints
curl https://cli-market-api.fly.dev/health/db
curl -X POST https://cli-market-api.fly.dev/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<MARKET_ADMIN_PASSWORD>"}'
```

## Automated script

```bash
export FLY_API_TOKEN='FlyV1 ...'
chmod +x ops/fly_pg_recover.sh
./ops/fly_pg_recover.sh
```

## GitHub Actions (workflow_dispatch)

1. Add repo secret `FLY_API_TOKEN` (org token with Postgres + SSH access).
2. Actions → **Fly PG Recovery** → Run workflow.

## If machines or volumes are missing

1. List all volumes (including detached):

   ```bash
   fly volumes list --app cli-market-db --all
   ```

2. If a volume exists but no machines, fork a new cluster from snapshot (last resort):

   ```bash
   fly postgres create --name cli-market-db-new --region gru \
     --fork-from cli-market-db:<volume-id>
   fly postgres attach cli-market-db-new --app cli-market-api
   fly postgres attach cli-market-db-new --app cli-market-collector
   ```

3. Update `DATABASE_URL` secret on API/collector if attach does not propagate.

## Symptoms while PG is down

| Signal | Expected degraded behavior |
|--------|---------------------------|
| `GET /health` | 200 `{"status":"healthy"}` |
| `GET /health/db` | 200, `backend: sqlite`, `pg_error` set |
| `GET /health/stats` | 200, zeros / empty moat |
| `POST /auth/login` | 401 (admin not in ephemeral SQLite) |
| `fly postgres list` | No active leader for `cli-market-db` |

## Related

- Initial migration: `ops/FLY-MIGRATION.md`
- Health endpoint fixes: PR #121 (`recover_pg_if_needed`, resilient `/health/db`)
