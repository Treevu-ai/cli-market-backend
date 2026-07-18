<!-- readme-hero -->
<div align="center">

<img src="assets/readme-hero.svg" alt="CLI MARKET BACKEND" width="100%" />

</div>

# cli-market-backend

> **⚠️ DEPRECATED** — This repo has been consolidated into [cli-market-world](https://github.com/Treevu-ai/cli-market-world).
>
> All backend-only features (AI agent discovery, HTTP MCP transport, MercadoPago checkout,
> semantic dashboard, retry helpers) have been ported to cli-market-world as of June 2026.
>
> **New development should happen in cli-market-world.** This repo is kept for reference
> and existing Fly.io deployments. Migrate to cli-market-world for the single source of truth.

> **Private repo** · Treevu-ai org — request access from maintainer.

FastAPI backend powering the [CLI Market](https://cli-market.dev) production API — 82 retailers (37 verified active) across 9 countries and VTEX · Shopify · Magento · WooCommerce, Mercado Pago + PayPal checkout. Live coverage/shelf-price counts: `GET /analytics/stats` (retailer/country counts also derive from the same `STORES` config as `market_stats.RETAILERS_VERIFIED`/`COUNTRIES` — update here only if that config's shape changes, not on every store added).

![Python 3.11](https://img.shields.io/badge/Python-3.11-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-latest-green) ![Fly.io](https://img.shields.io/badge/Deploy-Fly.io-8b5cf6) ![MIT](https://img.shields.io/badge/License-MIT-lightgrey)

---

## Quick start

```bash
pip install cli-market

export MARKET_API_URL=https://cli-market-api.fly.dev
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

Interactive docs: <https://cli-market-api.fly.dev/docs>

| Router | Prefix | Description |
|---|---|---|
| `auth` | `/auth` | Registration, login, API key issuance, plan gating |
| `search` | `/search` | Product search and cross-retailer price comparison |
| `retailers` | `/retailers` | Store catalog — 82 retailers (37 verified active), 9 countries, 4 platforms |
| `cart` | `/cart` | Cart CRUD, basket comparison across retailers |
| `orders` | `/orders` | Order history, reorder, status tracking |
| `payments` | `/payments` | PayPal · Mercado Pago · QR Yape/Plin checkout |
| `mercadopago` | `/checkout/mercadopago` | Mercado Pago Checkout Pro (PEN, webhooks) |
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

## Deployment (Fly.io)

The service is deployed via [`fly.toml`](fly.toml). The collector daemon uses [`fly.collector.toml`](fly.collector.toml) and runs [`collect_prices.py`](collect_prices.py) on a 4-hour cycle.

Required env vars on Fly.io (`fly secrets set ...`):

```
DATABASE_URL=postgresql://...
PORT=8080
MARKET_API_URL=https://cli-market-api.fly.dev
```

---

## Dev setup

```bash
pip install -r requirements.txt -r requirements-private.txt
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
| Free | $0 | 2,000 |
| Starter | $9/mo | 5,000 |
| Pro | $49/mo | 20,000 |
| Enterprise | Custom | Custom/SLAs |

*(Canónico: `cli-market-world/README.md` · `cli-market-world/docs/pricing-strategy.md`)*

Payments: PayPal · Mercado Pago · QR Yape/Plin.

FMCG WooCommerce pilot: see [docs/FMCG_PILOT_NUNAORGANICA.md](docs/FMCG_PILOT_NUNAORGANICA.md).

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

The `index_gate.py` bridge delegates to `cli-market-index` `IndexService` with persistent Golden Records (Postgres in production, SQLite in dev).

## Full docs

See the main repo for full documentation, MCP tool list, and integration guides:
<https://github.com/Treevu-ai/cli-market-world>

---

MIT License · [SINAPSIS INNOVADORA S.A.C.](https://cli-market.dev) · Lima, Peru
