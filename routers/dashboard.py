"""Dashboard — read-only operational views over the data moat.

Endpoints:
  GET /dashboard         HTML single-page dashboard (Chart.js)
  GET /dashboard/data    JSON feed consumed by the dashboard
  GET /dashboard/usage   Per-user tier + usage (requires auth)
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header

from market_core import STORES, TIERS, get_default_stores, db_get_subscription, get_db, price_to_usd
from market_basket import build_canasta_basica
from market_spread import build_spread_analytics, find_median_outliers
from dashboard_glossary import (
    LAYER_METRICS,
    build_metric_glossary,
)
from dashboard_quality import build_quality_funnel, count_flagged_discounts
from data_v1_service import count_flagged_outliers
from dashboard_renderer import render_dashboard_html
from dashboard_view_model import build_dashboard_view_model
from server_deps import require_admin, require_user

from .health import _age_hours, derive_collector_status

router = APIRouter(tags=["dashboard"])

# In-memory cache so the dashboard loads instantly for most visitors.
# _dashboard_data() is expensive (~15 SQL queries); caching for 120 s
# means only the first hit in each 2-min window pays the cost.
_dashboard_data_cache: dict | None = None
_dashboard_data_cache_at: float = 0.0
_DASHBOARD_CACHE_TTL = 120  # seconds


def _cached_dashboard_data() -> dict:
    global _dashboard_data_cache, _dashboard_data_cache_at
    # Self-heal: if we fell back to SQLite but Postgres is reachable again,
    # switch back so the dashboard reflects the real data moat (no restart).
    try:
        import market_core
        market_core.recover_pg_if_needed()
    except Exception:
        pass
    now = time.monotonic()
    if _dashboard_data_cache is not None and (now - _dashboard_data_cache_at) < _DASHBOARD_CACHE_TTL:
        return _dashboard_data_cache
    data = _dashboard_data()
    _dashboard_data_cache = data
    _dashboard_data_cache_at = now
    return data


def _build_moat_guide(
    *,
    total_indexed: int,
    unique_products: int,
    stores_indexed: int,
    snapshots_24h: int,
    stores_fresh_24h: int,
    coverage_7d_pct: float,
    fresh_24h_pct: float,
    moat_age_h: float | None,
    collector_status: str,
    collector_age_h: float | None,
    last_collected_at,
    last_run,
    catalog_stores: int,
    marketing_gate_pass: bool,
    stale_stores: list[str],
) -> dict:
    """Human-readable layers so agents and humans share one mental model."""
    last_ts = str(last_collected_at)[:19] if last_collected_at else None
    freshness_label = "fresh"
    if moat_age_h is None:
        freshness_label = "unknown"
    elif moat_age_h >= 24:
        freshness_label = "stale"
    elif moat_age_h >= 8:
        freshness_label = "aging"

    return {
        "headline": "Data Moat = inventario verificado + señales de mercado",
        "mental_model": [
            "Inventario: cuántos precios tenemos guardados en total (como un archivo histórico).",
            "Frescura: cuántos de esos precios se actualizaron en las últimas 24 h (¿sirven para decidir hoy?).",
            "Cobertura 7 días: de las tiendas que monitoreamos, ¿cuántas respondieron al menos una vez esta semana?",
            "Collector: robot que visita las tiendas cada pocas horas; si falla, el archivo sigue existiendo pero envejece.",
        ],
        "layers": [
            {
                "id": "inventory",
                "title": "1 · Inventario",
                "question": "¿Cuánto tenemos acumulado?",
                "metrics": {
                    "total_indexed": total_indexed,
                    "unique_products": unique_products,
                    "stores_indexed": stores_indexed,
                },
                "metric_help": LAYER_METRICS["inventory"],
                "note": "Stock de precios reales ya leídos de las webs de las tiendas. Sigue disponible aunque el collector esté en pausa.",
            },
            {
                "id": "freshness",
                "title": "2 · Frescura",
                "question": "¿Puedo confiar en el precio de hoy?",
                "metrics": {
                    "snapshots_24h": snapshots_24h,
                    "stores_fresh_24h": stores_fresh_24h,
                    "fresh_24h_pct": fresh_24h_pct,
                    "moat_age_hours": round(moat_age_h, 1) if moat_age_h is not None else None,
                    "last_collected_at": last_ts,
                    "status": freshness_label,
                },
                "metric_help": LAYER_METRICS["freshness"],
                "note": "Si «actualizaciones 24 h» es 0 pero hay inventario, los números son viejos — conviene esperar un refresh.",
            },
            {
                "id": "coverage",
                "title": "3 · Cobertura",
                "question": "¿Qué tan completo está el catálogo activo?",
                "metrics": {
                    "stores_active_catalog": catalog_stores,
                    "coverage_7d_pct": coverage_7d_pct,
                    "marketing_gate_pct": 80,
                    "marketing_gate_pass": marketing_gate_pass,
                    "stale_stores_sample": stale_stores,
                },
                "metric_help": LAYER_METRICS["coverage"],
                "note": "Meta interna: al menos 80 % de tiendas con datos en 7 días antes de campañas públicas.",
            },
            {
                "id": "agents",
                "title": "4 · Para agentes",
                "question": "¿Qué puedo hacer con esto?",
                "surfaces": [
                    {"cmd": "market compare <producto>", "use": "Buscar el precio más bajo del mismo producto en varias tiendas"},
                    {"cmd": "market basket", "use": "Calcular cuánto cuesta la canasta básica en cada tienda"},
                    {"cmd": "GET /v1/intel/inflation?days=7", "use": "Ver si los precios subieron o bajaron en la última semana"},
                    {"cmd": "GET /dashboard/data", "use": "Descargar todos los KPIs y explicaciones en JSON"},
                ],
            },
            {
                "id": "ops",
                "title": "5 · Collector (ops)",
                "question": "¿El pipeline de ingestión está sano?",
                "metrics": {
                    "collector_status": collector_status,
                    "collector_age_hours": round(collector_age_h, 1) if collector_age_h is not None else None,
                    "last_prices_collected": last_run["prices_collected"] if last_run else 0,
                    "last_stores_succeeded": last_run["stores_succeeded"] if last_run else 0,
                },
                "metric_help": LAYER_METRICS["ops"],
                "note": "Distinto de frescura: una corrida puede terminar con 0 precios si una tienda bloqueó el acceso.",
            },
        ],
    }


@router.get("/dashboard")
def dashboard():
    from fastapi.responses import HTMLResponse
    try:
        html = _static_dashboard()
    except Exception as e:
        import traceback
        html = f"<pre>STATIC DASHBOARD CRASH:\n{e}\n\n{traceback.format_exc()}</pre>"
    return HTMLResponse(html)


@router.get("/dashboard/data")
def dashboard_data():
    """Business-intelligence feed for the Data Moat dashboard."""
    try:
        return _cached_dashboard_data()
    except Exception as e:
        import traceback
        return {"error": str(e), "trace": traceback.format_exc()[-400:]}


@router.post("/dashboard/collector/trigger")
def collector_trigger(authorization: str | None = Header(None)):
    """Signal the collector daemon to run a cycle as soon as possible.

    Requires MARKET_API_TOKEN via Authorization: Bearer <token>.
    The daemon polls collector_triggers every ~30 s between cycles.
    Idempotent: duplicate triggers while one is pending are no-ops.
    """
    require_admin(authorization)
    db = get_db()
    # Check if there's already a pending (unfulfilled) trigger
    existing = db.execute(
        "SELECT id FROM collector_triggers WHERE fulfilled_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if existing:
        return {
            "triggered": False,
            "message": "Ya hay un trigger pendiente. El collector lo procesara en el proximo ciclo.",
            "pending_id": existing["id"],
        }

    db.execute(
        "INSERT INTO collector_triggers (source) VALUES ('dashboard')"
    )
    db.commit()
    return {
        "triggered": True,
        "message": "Trigger registrado. El collector ejecutara un ciclo en menos de 60 s (si el daemon esta corriendo).",
    }


def _dashboard_data():
    db = get_db()
    now = datetime.now(timezone.utc)
    cutoff_24h_sql = "datetime('now', '-24 hours')"

    # ── KPIs: moat size vs 24h refresh (distinct metrics) ─────────────────────
    total_indexed = db.execute(
        "SELECT COUNT(*) as n FROM price_snapshots WHERE price > 0 AND price < 999999"
    ).fetchone()["n"]

    unique_products = db.execute(
        "SELECT COUNT(DISTINCT product_id || store) as n FROM price_snapshots WHERE price > 0"
    ).fetchone()["n"]

    stores_indexed = db.execute(
        "SELECT COUNT(DISTINCT store) as n FROM price_snapshots WHERE price > 0"
    ).fetchone()["n"]

    last_collected_row = db.execute(
        "SELECT MAX(queried_at) as ts FROM price_snapshots WHERE price > 0"
    ).fetchone()
    last_collected_at = last_collected_row["ts"] if last_collected_row else None
    moat_age_h = _age_hours(last_collected_at) if last_collected_at else None

    snapshots_24h = db.execute(
        "SELECT COUNT(*) as n FROM price_snapshots WHERE price > 0 AND queried_at >= "
        + cutoff_24h_sql
    ).fetchone()["n"]

    active_stores_24h = db.execute(
        "SELECT COUNT(DISTINCT store) as n FROM price_snapshots WHERE price > 0 AND queried_at >= "
        + cutoff_24h_sql
    ).fetchone()["n"]

    total_runs = db.execute("SELECT COUNT(*) as n FROM collector_runs").fetchone()["n"]

    # ── By line ──────────────────────────────────────────────────────────────
    by_line = db.execute(
        """
        SELECT line, line_name,
               COUNT(DISTINCT product_id||store) as count,
               ROUND(AVG(price)::numeric, 2) as avg_price,
               ROUND(MIN(price)::numeric, 2) as min_price,
               ROUND(MAX(price)::numeric, 2) as max_price
        FROM price_snapshots WHERE price > 0 AND price < 999999
        GROUP BY line, line_name ORDER BY count DESC
        """
    ).fetchall()

    # ── By country ───────────────────────────────────────────────────────────
    by_country_raw = db.execute(
        "SELECT store, COUNT(*) as count FROM price_snapshots WHERE price > 0 GROUP BY store"
    ).fetchall()

    country_agg: dict[str, dict] = {}
    for r in by_country_raw:
        country = STORES.get(r["store"], {}).get("country", "??")
        c = country_agg.setdefault(country, {"country": country, "count": 0, "stores": set()})
        c["count"] += r["count"]
        c["stores"].add(r["store"])
    by_country = sorted(
        [{"country": c["country"], "count": c["count"], "stores": len(c["stores"])}
         for c in country_agg.values()],
        key=lambda x: x["count"], reverse=True,
    )

    # ── Dispersión por subcategoría + moneda (+ precio unitario cuando aplica) ─
    spread_rows = db.execute(
        """
        SELECT line, line_name, currency, category, name, brand, price,
               store, store_name, queried_at, url, product_id
        FROM price_snapshots WHERE price > 0 AND price < 999999
        """
    ).fetchall()
    spread_analytics = build_spread_analytics([dict(r) for r in spread_rows])
    dispersion = spread_analytics["dispersion"]
    canasta_spreads = spread_analytics["canasta_spreads"]
    marketing_spreads = spread_analytics["marketing_spreads"]
    for d in dispersion:
        d["avg_price_usd"] = price_to_usd(d.get("avg_price", 0), d.get("currency", ""))

    # ── By line + currency: Python-side percentiles (P25/P50/P75) ─────────────
    price_rows = db.execute(
        """SELECT line, line_name, currency, price, product_id, store
           FROM price_snapshots WHERE price > 0 AND price < 999999
           ORDER BY line, currency"""
    ).fetchall()

    groups: dict[tuple[str, str], dict] = {}
    for r in price_rows:
        key = (r["line"], r["currency"])
        if key not in groups:
            groups[key] = {
                "line": r["line"],
                "line_name": r["line_name"],
                "currency": r["currency"],
                "prices": [],
                "products": set(),
            }
        groups[key]["prices"].append(float(r["price"]))
        groups[key]["products"].add(f"{r['product_id']}|{r['store']}")

    def _percentile(sorted_prices: list[float], p: float) -> float:
        """Linear-interpolated percentile (PERCENTILE_CONT semantics)."""
        n = len(sorted_prices)
        if n == 0:
            return 0.0
        if n == 1:
            return round(sorted_prices[0], 2)
        idx = p * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return round(sorted_prices[lo] + frac * (sorted_prices[hi] - sorted_prices[lo]), 2)

    by_line_currency: list[dict] = []
    for key in sorted(groups):
        g = groups[key]
        prices = sorted(g["prices"])
        by_line_currency.append({
            "line": g["line"],
            "line_name": g["line_name"],
            "currency": g["currency"],
            "count": len(g["products"]),
            "p25": _percentile(prices, 0.25),
            "p50": _percentile(prices, 0.50),
            "p75": _percentile(prices, 0.75),
            "min_price": round(prices[0], 2),
            "max_price": round(prices[-1], 2),
        })

    # ── Normalized unit audit: per-category sample + non-normalizable counts ──
    from market_units import parse_pack_size

    unit_names_rows = db.execute(
        """SELECT line, currency, name FROM price_snapshots
           WHERE price > 0 AND price < 999999
           GROUP BY line, currency, name"""
    ).fetchall()

    group_names: dict[tuple[str, str], list[str]] = {}
    all_distinct: set[str] = set()
    for r in unit_names_rows:
        key = (r["line"], r["currency"])
        group_names.setdefault(key, []).append(r["name"])
        all_distinct.add(r["name"])

    parseable_total = sum(1 for n in all_distinct if parse_pack_size(n))
    non_normalizable_names = len(all_distinct) - parseable_total

    group_unit_info: dict[tuple[str, str], dict] = {}
    for key, names in group_names.items():
        total = len(names)
        parseable = sum(1 for n in names if parse_pack_size(n))
        normalizable_pct = round(parseable / total * 100, 1) if total > 0 else 0.0
        # Sample unit: first parseable name's base unit
        sample_unit = None
        for n in names:
            parsed = parse_pack_size(n)
            if parsed:
                sample_unit = parsed[1]
                break
        group_unit_info[key] = {
            "normalizable_pct": normalizable_pct,
            "normalized_unit": sample_unit,
        }

    for row in by_line_currency:
        key = (row["line"], row["currency"])
        info = group_unit_info.get(key, {"normalizable_pct": 0.0, "normalized_unit": None})
        row["normalizable_pct"] = info["normalizable_pct"]
        row["normalized_unit"] = info["normalized_unit"]

    # ── Top discounts (public: sane retail range only) ───────────────────────
    top_discounts = db.execute(
        """
        SELECT name, store_name, price, list_price, discount_pct, currency, line_name
        FROM (
            SELECT name, store_name, price, list_price,
                   ROUND(((1 - price / NULLIF(list_price,0)) * 100)::numeric) as discount_pct,
                   currency, line_name
            FROM price_snapshots
            WHERE list_price > price AND price > 0 AND list_price < 999999
        ) discounted
        WHERE discount_pct BETWEEN 5 AND 80
        ORDER BY discount_pct DESC LIMIT 10
        """
    ).fetchall()

    suspect_discounts = db.execute(
        """
        SELECT name, store_name, price, list_price, discount_pct, currency, line_name, confidence
        FROM (
            SELECT name, store_name, price, list_price,
                   ROUND(((1 - price / NULLIF(list_price,0)) * 100)::numeric) as discount_pct,
                   currency, line_name,
                   'suspect' as confidence
            FROM price_snapshots
            WHERE list_price > price AND price > 0 AND list_price < 999999
        ) discounted
        WHERE discount_pct >= 90
        ORDER BY discount_pct DESC LIMIT 20
        """
    ).fetchall()

    # ── Cheapest store by line ───────────────────────────────────────────────
    cheapest_by_line = db.execute(
        """
        SELECT line, line_name, store_name, ROUND(AVG(price)::numeric,2) as avg_price, currency, COUNT(*) as n
        FROM price_snapshots WHERE price > 0 AND price < 999999
          AND line_name IS NOT NULL AND line_name != ''
        GROUP BY line, line_name, store, store_name, currency ORDER BY line, avg_price ASC
        """
    ).fetchall()

    line_display: dict[str, str] = {
        "supermercados": "Supermercados",
        "farmacias": "Farmacias",
        "electro": "Electro",
        "moda": "Moda",
        "hogar": "Hogar",
        "departamentales": "Departamentales",
    }
    seen_lines: set[str] = set()
    cheapest_dedup: list[dict] = []
    for r in cheapest_by_line:
        ln = r["line_name"] or line_display.get(r.get("line", ""), r.get("line", "?"))
        if ln and ln not in seen_lines:
            seen_lines.add(ln)
            item = dict(r)
            item["line_name"] = ln
            cheapest_dedup.append(item)

    # ── Coverage ─────────────────────────────────────────────────────────────
    stores_24h = active_stores_24h

    # ── Price trend 7d ───────────────────────────────────────────────────────
    now7 = (now - timedelta(days=7)).isoformat()
    now14 = (now - timedelta(days=14)).isoformat()

    recent_prices = db.execute(
        """SELECT product_id, store, price, queried_at
           FROM price_snapshots WHERE price > 0 AND queried_at >= ?""",
        (now7,)
    ).fetchall()

    older_prices = db.execute(
        """SELECT product_id, store, price, queried_at
           FROM price_snapshots WHERE price > 0 AND queried_at >= ? AND queried_at < ?""",
        (now14, now7)
    ).fetchall()

    recent_map: dict[tuple, dict] = {}
    for r in recent_prices:
        key = (r["product_id"], r["store"])
        if key not in recent_map or r["queried_at"] > recent_map[key]["queried_at"]:
            recent_map[key] = dict(r)

    older_map: dict[tuple, dict] = {}
    for r in older_prices:
        key = (r["product_id"], r["store"])
        if key not in older_map or r["queried_at"] > older_map[key]["queried_at"]:
            older_map[key] = dict(r)

    movers: list[dict] = []
    for key, recent in recent_map.items():
        older = older_map.get(key)
        if older and older["price"] > 0:
            delta_pct = round((recent["price"] - older["price"]) / older["price"] * 100, 1)
            movers.append({
                "product_id": recent["product_id"],
                "store": recent["store"],
                "price_before": older["price"],
                "price_now": recent["price"],
                "delta_pct": delta_pct,
            })

    top_risers = sorted([m for m in movers if m["delta_pct"] > 0],
                        key=lambda x: x["delta_pct"], reverse=True)[:5]
    top_fallers = sorted([m for m in movers if m["delta_pct"] < 0],
                         key=lambda x: x["delta_pct"])[:5]

    # ── Collector status ─────────────────────────────────────────────────────
    last_run = db.execute(
        "SELECT started_at, finished_at, stores_succeeded, stores_attempted, prices_collected "
        "FROM collector_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()

    collector_status = "unknown"
    collector_age_h = None
    last_prices_collected = 0
    if last_run:
        finished = last_run["finished_at"]
        last_prices_collected = int(last_run["prices_collected"] or 0)
        if finished:
            collector_status, collector_age_h = derive_collector_status(
                finished_at=finished,
                prices_collected=last_prices_collected,
                moat_age_h=moat_age_h,
            )
        else:
            collector_status = "running"

    # ── Inventory daily (growth chart data) ────────────────────────────────────
    inventory_daily_rows = db.execute(
        """SELECT DATE(queried_at) as day, COUNT(*) as snapshots
           FROM price_snapshots WHERE price > 0
           GROUP BY DATE(queried_at) ORDER BY day DESC LIMIT 90"""
    ).fetchall()
    inventory_daily: list[dict] = [
        {"day": r["day"], "snapshots": int(r["snapshots"])}
        for r in inventory_daily_rows
    ]
    inventory_daily.reverse()  # chronological order

    # Derived growth stats
    first_snapshot_row = db.execute(
        "SELECT MIN(queried_at) as first FROM price_snapshots WHERE price > 0"
    ).fetchone()
    moat_start = first_snapshot_row["first"] if first_snapshot_row else None

    total_snapshots_all = db.execute(
        "SELECT COUNT(*) as n FROM price_snapshots WHERE price > 0"
    ).fetchone()["n"]

    daily_last_7d = [d["snapshots"] for d in inventory_daily[-7:]] if len(inventory_daily) >= 7 else []
    avg_daily_7d = round(sum(daily_last_7d) / 7, 1) if daily_last_7d else 0

    collector_interval_h = int(os.getenv("COLLECT_INTERVAL_HOURS", "8"))

    failing_stores = db.execute(
        "SELECT store, consecutive_failures FROM store_health "
        "WHERE consecutive_failures >= 3 ORDER BY consecutive_failures DESC"
    ).fetchall()
    failing_stores = [r for r in failing_stores if r["store"] in get_default_stores()]

    # ── Freshness ────────────────────────────────────────────────────────────
    freshness = db.execute(
        """SELECT store, store_name, MAX(queried_at) as last_seen
           FROM price_snapshots WHERE price > 0
           GROUP BY store, store_name ORDER BY last_seen DESC LIMIT 20"""
    ).fetchall()

    # ── Operacional: store health scores (active catalog only) ───────────────
    store_health_all = db.execute(
        """SELECT store, total_requests, total_successes,
                  CASE WHEN total_requests>0 THEN ROUND((total_successes*100.0/total_requests)::numeric,1) ELSE 0 END as success_pct,
                  consecutive_failures, last_success, last_error
           FROM store_health ORDER BY success_pct ASC, consecutive_failures DESC"""
    ).fetchall()
    store_health = [{k: r[k] for k in r.keys()} for r in store_health_all if r["store"] in get_default_stores()]
    healthy_count = sum(1 for h in store_health if float(h.get("success_pct") or 0) >= 80)

    # ── Per-store coverage 7d ─────────────────────────────────────────────────
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    store_coverage_rows = db.execute(
        """SELECT store,
                  COUNT(*) FILTER (WHERE queried_at >= ?) as snapshots_7d,
                  COUNT(*) as total_snapshots
           FROM price_snapshots WHERE price > 0
           GROUP BY store""",
        (cutoff_7d,),
    ).fetchall()
    store_coverage_map: dict[str, float] = {}
    for r in store_coverage_rows:
        sid = r["store"]
        total = int(r["total_snapshots"] or 0)
        store_coverage_map[sid] = round(int(r["snapshots_7d"] or 0) / total * 100, 1) if total > 0 else 0.0
    for h in store_health:
        h["coverage_7d_pct"] = store_coverage_map.get(h["store"], 0.0)

    # ── Moat summary (agent-facing, rolling windows) ─────────────────────────
    stores_fresh_24h_rows = db.execute(
        """SELECT store, MAX(queried_at) as last_seen, COUNT(*) as n
           FROM price_snapshots WHERE price > 0 AND queried_at >= """
        + cutoff_24h_sql
        + " GROUP BY store"
    ).fetchall()
    stores_fresh_24h = {r["store"] for r in stores_fresh_24h_rows if r["store"] in get_default_stores()}
    stores_active_7d_rows = db.execute(
        """SELECT store, COUNT(*) as n FROM price_snapshots
           WHERE price > 0 AND queried_at >= ? GROUP BY store""",
        (cutoff_7d,),
    ).fetchall()
    stores_active_7d = {r["store"] for r in stores_active_7d_rows if r["store"] in get_default_stores()}
    coverage_7d_pct = round(len(stores_active_7d) / len(get_default_stores()) * 100, 1) if get_default_stores() else 0
    fresh_24h_pct = round(len(stores_fresh_24h) / len(get_default_stores()) * 100, 1) if get_default_stores() else 0
    moat_stale = sorted(
        s for s in get_default_stores()
        if s not in stores_fresh_24h
    )[:10]

    # ── Operacional: collector run history ───────────────────────────────────
    collector_history = db.execute(
        """SELECT started_at, finished_at, stores_attempted, stores_succeeded,
                  prices_collected, errors
           FROM collector_runs ORDER BY id DESC LIMIT 10"""
    ).fetchall()

    # ── Exploratoria: price distribution ─────────────────────────────────────
    price_dist = db.execute(
        """SELECT
             CASE WHEN price<=10 THEN '0-10'
                  WHEN price<=50 THEN '10-50'
                  WHEN price<=100 THEN '50-100'
                  WHEN price<=500 THEN '100-500'
                  ELSE '500+'
             END as bucket,
             COUNT(*) as count
           FROM price_snapshots WHERE price>0 AND price<999999
           GROUP BY bucket ORDER BY MIN(price)"""
    ).fetchall()

    # ── Exploratoria: line-country matrix ────────────────────────────────────
    line_country_raw = db.execute(
        """SELECT ps.line, ps.store, COUNT(*) as n
           FROM price_snapshots ps WHERE ps.price>0
           GROUP BY ps.line, ps.store"""
    ).fetchall()
    line_country_map: dict[str, set] = {}
    for r in line_country_raw:
        country = STORES.get(r["store"], {}).get("country", "??")
        key = f"{r['line']}|{country}"
        line_country_map.setdefault(key, set()).add(r["store"])
    line_country_matrix = [{"line": k.split("|")[0], "country": k.split("|")[1], "stores": len(v)}
                           for k, v in line_country_map.items()]

    # ── Exploratoria: products per store ─────────────────────────────────────
    products_per_store = db.execute(
        """SELECT store_name, store, COUNT(DISTINCT product_id) as unique_products,
                  COUNT(*) as total_snapshots
           FROM price_snapshots WHERE price>0
           GROUP BY store, store_name ORDER BY unique_products DESC LIMIT 15"""
    ).fetchall()

    # ── Outliers: bidirectional vs group median (not mean) ───────────────────
    spread_products = [dict(r) for r in spread_rows]
    outliers = find_median_outliers(spread_products, min_group=5, band=5.0, limit=10)
    for item in outliers:
        item["price_usd"] = price_to_usd(item.get("price", 0), item.get("currency", ""))

    # Enrich outliers with store health state at capture time
    outlier_stores = list({o["store"] for o in outliers if o.get("store")})
    if outlier_stores:
        store_health_lookup = {}
        for sh in db.execute(
            """SELECT store, total_requests, total_successes,
                      CASE WHEN total_requests>0 THEN ROUND((total_successes*100.0/total_requests)::numeric,1)
                           ELSE 0 END as success_pct,
                      consecutive_failures
               FROM store_health WHERE store IN ({})""".format(
                ",".join("?" * len(outlier_stores))
            ),
            outlier_stores,
        ).fetchall():
            sid = sh["store"]
            pct = float(sh["success_pct"] or 0)
            store_health_lookup[sid] = (
                "dead" if pct < 30 else ("ok" if pct >= 80 else "partial")
            )
        for o in outliers:
            o["store_health_state"] = store_health_lookup.get(o.get("store", ""), "unknown")

    # ── Inflation 7d: per line + currency (nominal + USD-normalized) ────────
    inflation = []
    for row in by_line_currency:
        recent_avg = db.execute(
            """SELECT ROUND(AVG(price)::numeric,2) as avg_price
               FROM price_snapshots
               WHERE line=? AND currency=? AND price>0 AND price<999999 AND queried_at >= ?""",
            (row["line"], row["currency"], now7),
        ).fetchone()
        older_avg = db.execute(
            """SELECT ROUND(AVG(price)::numeric,2) as avg_price
               FROM price_snapshots
               WHERE line=? AND currency=? AND price>0 AND price<999999
                 AND queried_at >= ? AND queried_at < ?""",
            (row["line"], row["currency"], now14, now7),
        ).fetchone()
        r_avg = float(recent_avg["avg_price"] or 0) if recent_avg else 0.0
        o_avg = float(older_avg["avg_price"] or 0) if older_avg else 0.0
        delta = round((r_avg - o_avg) / o_avg * 100, 1) if o_avg > 0 else 0
        cur = row["currency"] or ""
        inflation.append({
            "line": row["line_name"] or row["line"],
            "line_key": row["line"],
            "currency": cur,
            "avg_now": r_avg,
            "avg_before": o_avg,
            "delta_pct": delta,
            "avg_now_usd": price_to_usd(r_avg, cur),
            "avg_before_usd": price_to_usd(o_avg, cur),
        })

    # ── Canasta basica: fixed 10 products compared across top stores ─────────
    canasta_basica = build_canasta_basica(db, min_items=3)

    flagged_discounts = count_flagged_discounts(db)
    flagged_outliers = count_flagged_outliers(db)

    # ── Confidence distribution (ok / suspect) ─────────────────────────────────
    confidence_dist: dict[str, int] = {}
    try:
        from market_db import price_snapshots_has_confidence
        if price_snapshots_has_confidence(db):
            conf_rows = db.execute(
                """SELECT confidence, COUNT(*) as n
                   FROM price_snapshots WHERE price > 0
                   GROUP BY confidence"""
            ).fetchall()
            confidence_dist = {r["confidence"]: r["n"] for r in conf_rows}
    except Exception:
        pass

    indicator_latest: list[dict] = []
    enrichment_latest: list[dict] = []
    indicator_by_country: dict[str, list[dict]] = {}
    try:
        from market_indicators import ENRICHMENT_INDICATOR_KEYS, get_latest_values

        # Fetch per-country enrichment for all 8 countries
        enrichment_keys = set(ENRICHMENT_INDICATOR_KEYS)
        all_countries = sorted({s["country"] for s in STORES.values() if s.get("country")})
        for cc in all_countries:
            cc_vals = get_latest_values(db, country=cc, limit=30)
            enrichment_latest.extend(v for v in cc_vals if v.get("key") in enrichment_keys)
            indicator_by_country[cc] = cc_vals
        indicator_latest = get_latest_values(db, limit=200)
    except Exception:
        pass

    db.close()

    quality_funnel = build_quality_funnel(
        captured=total_indexed,
        flagged_discounts=flagged_discounts,
        flagged_outliers=flagged_outliers,
        citable=len(marketing_spreads),
    )
    quality_funnel["non_normalizable_names"] = non_normalizable_names
    quality_funnel["confidence_dist"] = confidence_dist

    moat_guide = _build_moat_guide(
        total_indexed=total_indexed,
        unique_products=unique_products,
        stores_indexed=stores_indexed,
        snapshots_24h=snapshots_24h,
        stores_fresh_24h=len(stores_fresh_24h),
        coverage_7d_pct=coverage_7d_pct,
        fresh_24h_pct=fresh_24h_pct,
        moat_age_h=moat_age_h,
        collector_status=collector_status,
        collector_age_h=collector_age_h,
        last_collected_at=last_collected_at,
        last_run=last_run,
        catalog_stores=len(get_default_stores()),
        marketing_gate_pass=coverage_7d_pct >= 80,
        stale_stores=moat_stale,
    )

    result = {
        "generated_at": now.isoformat(),
        "kpis": {
            "total_indexed": total_indexed,
            "unique_products": unique_products,
            "stores_indexed": stores_indexed,
            "total_snapshots": snapshots_24h,
            "snapshots_24h": snapshots_24h,
            "active_stores": active_stores_24h,
            "active_stores_24h": active_stores_24h,
            "total_stores": len(get_default_stores()),
            "catalog_stores": len(STORES),
            "healthy_stores": healthy_count,
            "store_success_pct": round(healthy_count / len(get_default_stores()) * 100, 1) if get_default_stores() else 0,
            "coverage_7d_pct": coverage_7d_pct,
            "stores_fresh_24h": len(stores_fresh_24h),
            "fresh_24h_pct": fresh_24h_pct,
            "total_runs": total_runs,
            "stores_24h": stores_24h,
            "last_collected_at": str(last_collected_at) if last_collected_at else None,
            "moat_age_hours": round(moat_age_h, 1) if moat_age_h is not None else None,
        },
        "moat_summary": {
            "purpose": "Verified cross-retailer prices for agent compare, basket, and inflation signals.",
            "refresh_hours": 8,
            "total_indexed": total_indexed,
            "unique_products": unique_products,
            "stores_indexed": stores_indexed,
            "snapshots_24h": snapshots_24h,
            "last_collected_at": str(last_collected_at) if last_collected_at else None,
            "moat_age_hours": round(moat_age_h, 1) if moat_age_h is not None else None,
            "collector_stale": moat_age_h is not None and moat_age_h >= 24,
            "stores_active_catalog": len(get_default_stores()),
            "stores_fresh_24h": len(stores_fresh_24h),
            "stores_active_7d": len(stores_active_7d),
            "coverage_7d_pct": coverage_7d_pct,
            "fresh_24h_pct": fresh_24h_pct,
            "marketing_gate_pct": 80,
            "marketing_gate_pass": coverage_7d_pct >= 80,
            "stale_stores": moat_stale,
            "agent_surfaces": [
                "market compare", "market basket", "market indicators", "market scores",
                "market enrichment", "market enrichment --refresh",
                "/v1/intel/inflation", "/v1/intel/scores", "/v1/intel/enrichment",
                "/v1/intel/enrichment/subcategories", "/analytics/indicators", "MCP market_stats",
            ],
        },
        "moat_guide": moat_guide,
        "metric_glossary": build_metric_glossary(),
        "by_line": [dict(r) for r in by_line],
        "by_country": by_country,
        "dispersion": dispersion,
        "canasta_spreads": canasta_spreads,
        "marketing_spreads": marketing_spreads,
        "analytics_meta": {
            "fx_reference": "USD",
            "fx_source": "static_pen_table",
            "dispersion_grouping": "line+currency+subcategory",
            "dispersion_crit_count": spread_analytics["dispersion_crit_count"],
            "marketing_crit_count": spread_analytics["marketing_crit_count"],
            "marketing_canasta_min_spread": spread_analytics.get("marketing_canasta_min_spread"),
        },
        "top_discounts": [dict(r) for r in top_discounts],
        "suspect_discounts": [dict(r) for r in suspect_discounts],
        "cheapest_by_line": cheapest_dedup,
        "top_risers": top_risers,
        "top_fallers": top_fallers,
        "collector": {
            "status": collector_status,
            "age_hours": round(collector_age_h, 1) if collector_age_h else None,
            "interval_hours": collector_interval_h,
            "last_run": last_run["started_at"] if last_run else None,
            "last_finished": last_run["finished_at"] if last_run else None,
            "stores_succeeded": last_run["stores_succeeded"] if last_run else 0,
            "prices_collected": last_prices_collected,
            "last_prices_collected": last_prices_collected,
        },
        "inventory_daily": inventory_daily,
        "moat_start": moat_start,
        "total_snapshots_all": total_snapshots_all,
        "avg_daily_snapshots_7d": avg_daily_7d,
        "failing_stores": [dict(r) for r in failing_stores],
        "freshness": [dict(r) for r in freshness],
        # ── Operacional ──────────────────────────────────────────────────────
        "store_health": [dict(r) for r in store_health],
        "collector_history": [dict(r) for r in collector_history],
        # ── Exploratoria ─────────────────────────────────────────────────────
        "price_distribution": [dict(r) for r in price_dist],
        "line_country_matrix": line_country_matrix,
        "products_per_store": [dict(r) for r in products_per_store],
        # ── Weak signal ──────────────────────────────────────────────────────
        "outliers": outliers,
        # ── Analytics ────────────────────────────────────────────────────────
        "inflation": inflation,
        "canasta_basica": canasta_basica,
        "quality_funnel": quality_funnel,
        "by_line_currency": [dict(r) for r in by_line_currency],
        "indicators": {
            "latest": indicator_latest,
            "enrichment": enrichment_latest,
            "by_country": indicator_by_country,
            "auto_refresh": True,
            "docs": "docs/DATA-MOAT-INDICATORS.md",
        },
    }
    result["dashboard_view"] = build_dashboard_view_model(result)
    return result


def _static_dashboard() -> str:
    """Server-rendered dashboard — single renderer from dashboard_view + metric_glossary."""
    try:
        data = _cached_dashboard_data()
    except Exception as e:
        import traceback
        return f"<pre>ERROR: {e}\n{traceback.format_exc()}</pre>"
    if "error" in data:
        return (
            f"<html><body style='background:#0a0a0a;color:#ff4444;"
            f"font:12px monospace;padding:20px'><pre>{data['error']}\n"
            f"{data.get('trace', '')}</pre></body></html>"
        )
    return render_dashboard_html(data)


@router.get("/dashboard/usage")
def dashboard_usage(authorization: str | None = Header(None)):
    """Per-user usage view."""
    username = require_user(authorization)
    sub = db_get_subscription(username)
    tier = sub.get("tier", "free")
    limits = TIERS.get(tier, TIERS["free"])
    db = get_db()
    today_reqs = (
        db.execute(
            "SELECT SUM(counter) as n FROM rate_limits "
            "WHERE key LIKE ? AND window_start >= ?",
            ("%:daily", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        ).fetchone()["n"] or 0
    )
    keys = db.execute(
        "SELECT COUNT(*) as n FROM api_keys WHERE username=?", (username,)
    ).fetchone()["n"]
    db.close()
    return {
        "username": username,
        "tier": tier,
        "limits": {
            "req_min": limits["req_min"] or "unlimited",
            "req_day": limits["req_day"] or "unlimited",
            "checkout": limits["checkout"],
        },
        "usage": {"requests_today": today_reqs, "api_keys_used": keys},
    }
