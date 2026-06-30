# Migración Railway → Fly.io

## Pre-requisitos

```powershell
# Instalar flyctl
winget install Fly.io.flyctl
# o: iwr https://fly.io/install.ps1 -useb | iex

fly auth login
```

## 1. Crear apps y base de datos

```powershell
# Crear las dos apps
fly apps create cli-market-api
fly apps create cli-market-collector

# Crear Postgres en São Paulo (más cerca de PE/MX/CO/AR/CL)
fly postgres create --name cli-market-db --region gru --vm-size shared-cpu-1x --volume-size 10

# Adjuntar Postgres a ambas apps (inyecta DATABASE_URL automáticamente)
fly postgres attach cli-market-db --app cli-market-api
fly postgres attach cli-market-db --app cli-market-collector
```

## 2. Volumen de datos para la API

```powershell
fly volumes create cli_market_data --app cli-market-api --region gru --size 1
```

## 3. Secrets

```powershell
# API
fly secrets set `
  GITHUB_TOKEN=<PAT-con-repo-read-en-cli-market-index> `
  CLI_MARKET_API_KEY=<key> `
  SLACK_WEBHOOK_BITACORA=<webhook> `
  --app cli-market-api

# Collector
fly secrets set `
  GITHUB_TOKEN=<PAT-con-repo-read-en-cli-market-index> `
  COLLECT_PARALLEL=6 `
  COLLECT_DELAY=0.75 `
  COLLECT_MAX_QUERIES_PER_LINE=12 `
  COLLECT_INTERVAL_HOURS=4 `
  --app cli-market-collector
```

> Copiar el resto de variables desde Railway Dashboard → Variables del servicio API.

## 4. Deploy API

```powershell
cd ~\cli-market-backend
fly deploy --app cli-market-api --config fly.toml --build-arg GITHUB_TOKEN=<PAT>
```

Verificar:
```powershell
fly status --app cli-market-api
fly logs --app cli-market-api
curl https://cli-market-api.fly.dev/health
```

## 5. Deploy Collector

```powershell
fly deploy --app cli-market-collector --config fly.collector.toml --build-arg GITHUB_TOKEN=<PAT>
```

Verificar que el daemon esté corriendo:
```powershell
fly logs --app cli-market-collector --tail
```

## 6. Migrar datos de Postgres (si Railway tiene datos que conservar)

```powershell
# En Railway: obtener DATABASE_URL del servicio Postgres
# Dump de Railway
pg_dump <RAILWAY_DATABASE_URL> > backup.sql

# Restore en Fly Postgres
fly postgres connect --app cli-market-db
# En el shell de psql:
# \i backup.sql
# \q

# O directamente:
fly proxy 5432 --app cli-market-db &
psql postgresql://postgres:<password>@localhost:5432/postgres < backup.sql
```

## 7. Actualizar DNS / dominio

Actualmente: `cli-market-production.up.railway.app`
Nuevo: `cli-market-api.fly.dev` (o dominio custom)

```powershell
# Agregar dominio custom
fly certs create cli-market-production.tu-dominio.com --app cli-market-api
```

Actualizar en:
- `cli-market-world/landing/lib/api.ts` (API_URL)
- `cli-market-content/CLAUDE.md` (API base URL)
- `procure-copilot/.env` (CLI_MARKET_API_URL)
- `ops/daily_briefing.py` (si tiene URL hardcoded)

## 8. Smoke test post-migración

```powershell
cd ~\cli-market-content
# Cambiar temporalmente API_URL en .env a Fly
make gate-remote
py scripts\smoke_pir.py  # si hay JSON reciente
npm run smoke            # desde procure-copilot
```

## Costos estimados Fly.io vs Railway

| Componente | Railway | Fly.io |
|------------|---------|--------|
| API (shared-cpu-1x 512MB) | ~$10/mes | ~$3.19/mes |
| Collector (shared-cpu-2x 1GB) | ~$15/mes | ~$6.12/mes |
| Postgres (1GB) | ~$5/mes | ~$5/mes (Fly managed) |
| **Total estimado** | **~$30/mes** | **~$14/mes** |

## Notas

- `auto_stop_machines = false` en la API para evitar cold starts (crítico para el collector que hace queries a la API)
- El collector usa Playwright/Chromium — necesita `shared-cpu-2x` mínimo en Fly para no OOM
- Fly.io tiene `fly scale count 0` para pausar el collector manualmente si hay problemas
- Fly reinicia el proceso automáticamente con `policy = "always"` si muere

## Rollback

Si algo falla, Railway sigue corriendo en paralelo hasta que apagues los servicios manualmente.
No hay downtime forzado — puedes migrar el tráfico gradualmente cambiando la variable `CLI_MARKET_API_URL` en cada cliente.
