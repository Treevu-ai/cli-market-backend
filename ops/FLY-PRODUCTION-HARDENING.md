# Fly.io production hardening — DB + collector estable

## Diagnóstico (jun 2026)

| Componente | Estado típico del fallo | Causa raíz |
|------------|----------------------|------------|
| `cli-market-db` primary | `1/3` checks, `connection refused` en repmgr | **256MB RAM** — postgres-flex sin memoria para leader election |
| `cli-market-api` | `/health` 200 pero `/health/db` 500 | API cae a SQLite o no conecta PG tras fallo de primary |
| `cli-market-collector` | Moat deja de crecer | Sin PG estable no persiste `price_snapshots` |

## Objetivo

- **DB:** PostgreSQL siempre líder activo, ≥1GB RAM en primary, réplica en gru
- **API:** `MARKET_ENV=production` — no fallback silencioso a SQLite
- **Collector:** app separada `cli-market-collector`, 1GB RAM, ciclo cada 4h
- **Ops:** watchdog cada 10 min reinicia PG si `/health/db` ≠ postgresql

## Tamaños recomendados (mínimo producción)

| App | VM | RAM | Notas |
|-----|-----|-----|-------|
| `cli-market-db` (primary) | `shared-cpu-1x` | **1024mb** | Subir desde 256mb |
| `cli-market-db` (replica) | `shared-cpu-1x` | 512mb+ | Opcional igualar a primary |
| `cli-market-api` | `shared-cpu-1x` | 512mb | OK actual |
| `cli-market-collector` | `shared-cpu-2x` | 1024mb | Playwright |

## Escalar primary Postgres (PowerShell)

```powershell
fly machine update d8d1169f360598 -a cli-market-db --vm-memory 1024
```

Verificar `3/3` checks antes de seguir.

## Collector estable

```powershell
fly status -a cli-market-collector
fly logs -a cli-market-collector --no-tail | Select-Object -Last 30
```

Si no existe la app, seguir `ops/FLY-MIGRATION.md` §5.

## Watchdog automático

- Workflow: `.github/workflows/fly-pg-watchdog.yml` (cada 10 min)
- Secret requerido: `FLY_API_TOKEN`
- Manual: Actions → **Fly PG Watchdog** o `ops/fly_pg_recover.sh`

## Verificación post-cambio

```powershell
Invoke-RestMethod "https://cli-market-api.fly.dev/health/db"
# backend: postgresql, snapshots > 0, pg_error vacío

Invoke-RestMethod "https://cli-market-api.fly.dev/health/collector"
# collector_status: ok | stale (no dead)
```

## Escalar más adelante

- Más retailers / QPS: `fly scale count 2 -a cli-market-api` + connection pooling
- Moat >500K snapshots: volumen PG 20GB+, `shared-cpu-2x` en primary
- Managed Postgres Fly (cuando disponible en la org) vs postgres-flex unmanaged
