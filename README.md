# cli-market-backend

[![CI](https://github.com/Treevu-ai/cli-market-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/Treevu-ai/cli-market-backend/actions/workflows/ci.yml)

FastAPI backend powering the [CLI Market](https://cli-market.dev) production API — 66 retailers, 45,000+ shelf prices, 43 MCP tools.

![Python 3.11](https://img.shields.io/badge/Python-3.11-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-latest-green) ![Railway](https://img.shields.io/badge/Deploy-Railway-blueviolet) ![MIT](https://img.shields.io/badge/License-MIT-lightgrey)

---

## Quick start

```bash
pip install cli-market

export MARKET_API_URL=https://cli-market-production.up.railway.app
market login
market search "leche" --country PE
```

Key env vars (local):

| Variable | Description |
|---|---|
| `DATABASE_URL` | Postgres connection string |
| `PORT` | Server port (default `8765`) |
| `MARKET_API_URL` | Public API base URL |

---

## API overview

Interactive docs: <https://cli-market-production.up.railway.app/docs>

| Router | Prefix | Description |
|---|---|---|
| `auth` | `/auth` | Registration, login, API key issuance, plan gating |
| `search` | `/search` | Product search and cross-retailer price comparison |
| `retailers` | `/retailers` | Store catalog — 66 retailers (36 verified), 8 countries |
| `cart` | `/cart` | Cart CRUD, basket comparison across retailers |
| `orders` | `/orders` | Order history, reorder, status tracking |
| `payments` | `/payments` | PayPal + QR Yape/Plin checkout flows |
| `alerts` | `/alerts` | Price alert CRUD (email + webhook) |
| `analytics` | `/analytics` | Usage stats, request metrics per API key |
| `dashboard` | `/dashboard` | Live coverage, scrape quality, P25/P50/P75 spreads |
| `intel` | `/intel` | AI agent intel queries (plan-gated) |
| `data_v1` | `/v1` | Core data endpoints: prices, categories, enrichment |
| `data_export` | `/export` | CSV export + cron feeds (Starter+) |
| `media` | `/media` | Product image assets |
| `admin` | `/admin` | Internal admin — user management, scraper control |
| `retailer_admin` | `/retailer-admin` | Retailer-side portal (catalog, inventory) |
| `agent` | `/agent` | MCP tool entrypoints (43 tools) |
| `health` | `/health` | Liveness + readiness probes |
| `misc` | `/misc` | Countries, currencies, exchange rates, misc lookups |

---

## Deployment (Railway)

The service is deployed via [`railway.toml`](railway.toml). The collector daemon uses [`railway.collector.toml`](railway.collector.toml) and runs [`collect_prices.py`](collect_prices.py) on a 4-hour cycle.

Required env vars on Railway:

```
DATABASE_URL=postgresql://...
PORT=8765
MARKET_API_URL=https://cli-market-production.up.railway.app
```

---

## Dev setup

```bash
pip install -r requirements.txt
uvicorn market_server:app --reload --port 8765
```

Tests:

```bash
pytest tests/
```

---

## Pricing

| Plan | Price | Requests/day |
|---|---|---|
| Free | $0 | 1,000 |
| Starter | $29/mo | 5,000 |
| Pro | $79/mo | 10,000 |
| Builder | $149/mo | 50,000 |
| Enterprise | Custom | Unlimited |

Payments: PayPal + QR Yape/Plin.

---

## Semantic enrichment pipeline

Every product that passes through the API is enriched with canonical identities from `cli-market-index`:

```
collect_prices.py (4h cycle)
       |
       v
  search / orders routers
       |
       v
  index_gate.enrich_list()   -- identical to cli-market-index/index_gate.py
       |
       v
  normalized units + brands  -- from cli-market-index normalizers (single source of truth)
       |
       v
  'index' block in response  -> { id, canonical_name, confidence, measurement }
```

The `index_gate.py` bridge imports normalizers directly from `cli-market-index` as the canonical source.

## Full docs

See the main repo for full documentation, MCP tool list, and integration guides:
<https://github.com/Treevu-ai/cli-market-world>

---

MIT License · [SINAPSIS INNOVADORA S.A.C.](https://cli-market.dev) · Lima, Peru
