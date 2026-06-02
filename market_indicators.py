"""Data moat indicators — catalog, external API fetchers, and computed scores.

Indicators are stored in ``indicator_definitions`` + ``indicator_values``.
Computed signals derive from ``price_snapshots``, ``price_history``, and
``search_queries``. External macro signals use public APIs with graceful fallback.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from market_core import STORES, get_db
from market_spread import CANASTA_ITEMS

logger = logging.getLogger(__name__)

# ── Catalog ───────────────────────────────────────────────────────────────────

INDICATOR_DEFINITIONS: list[dict[str, Any]] = [
    {
        "key": "promo_intensity",
        "name": "Promo Intensity",
        "category": "retail",
        "source": "internal:price_snapshots",
        "unit": "pct",
        "refresh_hours": 8,
        "description": "Share of indexed SKUs with an active discount (list_price > price).",
        "formula": "count(discount>0) / count(price>0) * 100",
    },
    {
        "key": "price_dispersion",
        "name": "Price Dispersion",
        "category": "retail",
        "source": "internal:price_snapshots",
        "unit": "pct",
        "refresh_hours": 8,
        "description": "Average coefficient of variation for comparable product names across stores.",
        "formula": "mean(std(prices)/mean(prices)) * 100 per product cluster",
    },
    {
        "key": "basket_stress_index",
        "name": "Basket Stress Index",
        "category": "affordability",
        "source": "internal:price_snapshots",
        "unit": "index",
        "refresh_hours": 8,
        "description": "Minimum canasta básica total (10 staples) vs rolling baseline in price_history.",
        "formula": "min_canasta_total / baseline_canasta_total * 100",
    },
    {
        "key": "search_momentum",
        "name": "Search Momentum",
        "category": "demand",
        "source": "internal:search_queries",
        "unit": "ratio",
        "refresh_hours": 24,
        "description": "Ratio of search volume last 7d vs prior 7d.",
        "formula": "searches_7d / max(searches_prev_7d, 1)",
    },
    {
        "key": "moat_freshness",
        "name": "Moat Freshness",
        "category": "quality",
        "source": "internal:price_snapshots",
        "unit": "pct",
        "refresh_hours": 8,
        "description": "Share of price_snapshots updated within the last 24 hours.",
        "formula": "count(queried_at>=now-24h) / count(*) * 100",
    },
    {
        "key": "store_coverage",
        "name": "Store Coverage",
        "category": "quality",
        "source": "internal:price_snapshots",
        "unit": "count",
        "refresh_hours": 8,
        "description": "Distinct stores with at least one valid price in the moat.",
        "formula": "count(distinct store where price>0)",
    },
    {
        "key": "fx_usd_local",
        "name": "USD Exchange Rate",
        "category": "macro",
        "source": "external:open.er-api.com",
        "unit": "rate",
        "refresh_hours": 24,
        "description": "Latest USD to local currency rate (public FX API).",
        "formula": "GET https://open.er-api.com/v6/latest/USD → rates[CCY]",
    },
    {
        "key": "cpi_official_yoy",
        "name": "Official CPI YoY",
        "category": "macro",
        "source": "external:worldbank",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "World Bank FP.CPI.TOTL.ZG — annual inflation, official benchmark.",
        "formula": "World Bank API indicator FP.CPI.TOTL.ZG",
    },
    {
        "key": "food_price_index",
        "name": "Food Price Index",
        "category": "macro",
        "source": "external:worldbank",
        "unit": "index",
        "refresh_hours": 168,
        "description": "World Bank AG.PRD.FOOD.XD — food production price index proxy.",
        "formula": "World Bank API indicator AG.PRD.FOOD.XD",
    },
    {
        "key": "collector_vs_official_gap",
        "name": "Collector vs Official Gap",
        "category": "composite",
        "source": "computed",
        "unit": "pp",
        "refresh_hours": 24,
        "description": "Difference between internal shelf inflation signal and official CPI YoY.",
        "formula": "internal_inflation_avg_pct - cpi_official_yoy",
    },
    {
        "key": "off_match_rate",
        "name": "Open Food Facts Match Rate",
        "category": "product",
        "source": "external:openfoodfacts",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "Share of sampled supermercados SKUs matched in Open Food Facts.",
        "formula": "matched_samples / ENRICH_SAMPLE_SIZE * 100",
    },
    {
        "key": "off_nutriscore_ab_pct",
        "name": "Nutri-Score A/B Share",
        "category": "product",
        "source": "external:openfoodfacts",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "Among OFF matches, percentage with Nutri-Score A or B.",
        "formula": "count(nutriscore in A,B) / matched * 100",
    },
    {
        "key": "off_nova_avg",
        "name": "NOVA Group Average",
        "category": "product",
        "source": "external:openfoodfacts",
        "unit": "score",
        "refresh_hours": 168,
        "description": "Average NOVA processing group (1=minimal … 4=ultra-processed).",
        "formula": "mean(nova_group) over OFF matches",
    },
    {
        "key": "wiki_demand_momentum",
        "name": "Wikipedia Demand Momentum",
        "category": "demand",
        "source": "external:wikimedia",
        "unit": "ratio",
        "refresh_hours": 24,
        "description": "7d vs prior 7d pageviews for staple food terms (country wiki).",
        "formula": "mean(recent_views / prior_views) per article",
    },
    {
        "key": "weather_logistics_stress",
        "name": "Weather Logistics Stress",
        "category": "logistics",
        "source": "external:open-meteo.com",
        "unit": "index",
        "refresh_hours": 12,
        "description": "7-day precipitation + heat stress proxy for capital city (fresh goods logistics).",
        "formula": "min(100, rain_mm*2.5 + heat_days*8)",
    },
    {
        "key": "food_cpi_yoy",
        "name": "Official Food CPI YoY",
        "category": "macro",
        "source": "external:worldbank",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "World Bank FP.CPI.FOOD.ZG — food inflation official benchmark.",
        "formula": "World Bank API FP.CPI.FOOD.ZG",
    },
    {
        "key": "off_ultra_processed_pct",
        "name": "Ultra-Processed Share (NOVA 4)",
        "category": "product",
        "source": "external:openfoodfacts",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "Among OFF matches, percentage classified NOVA group 4 (ultra-processed).",
        "formula": "count(nova_group=4) / matched * 100",
    },
    {
        "key": "food_inflation_spread",
        "name": "Food vs Headline CPI Spread",
        "category": "composite",
        "source": "computed",
        "unit": "pp",
        "refresh_hours": 168,
        "description": "Official food CPI YoY minus headline CPI YoY — food inflation premium.",
        "formula": "food_cpi_yoy - cpi_official_yoy",
    },
    {
        "key": "wiki_staple_momentum",
        "name": "Staple Wikipedia Momentum",
        "category": "demand",
        "source": "external:wikimedia",
        "unit": "ratio",
        "refresh_hours": 24,
        "description": "7d vs prior 7d pageviews for leche/arroz/aceite articles.",
        "formula": "mean(recent/prior) for staple wiki articles",
    },
    {
        "key": "staple_price_momentum",
        "name": "Staple Price Momentum",
        "category": "affordability",
        "source": "internal:price_history",
        "unit": "pct",
        "refresh_hours": 8,
        "description": "Avg price change over 7d for canasta staple SKUs in price_history.",
        "formula": "mean((last-first)/first*100) for staple products",
    },
    {
        "key": "off_ecoscore_avg",
        "name": "Eco-Score Average",
        "category": "product",
        "source": "external:openfoodfacts",
        "unit": "score",
        "refresh_hours": 168,
        "description": "Average Eco-Score (A=5 … E=1) among OFF matches.",
        "formula": "mean(ecoscore mapped 1-5) over OFF matches",
    },
    {
        "key": "subcat_price_momentum",
        "name": "Subcategory Price Momentum",
        "category": "affordability",
        "source": "internal:price_history",
        "unit": "pct",
        "refresh_hours": 8,
        "description": "7d price change for a canasta subcategory (scope: {CC}:subcat:{item}).",
        "formula": "mean delta_pct per SKU in subcategory bucket",
    },
    {
        "key": "subcat_wiki_momentum",
        "name": "Subcategory Wiki Momentum",
        "category": "demand",
        "source": "external:wikimedia",
        "unit": "ratio",
        "refresh_hours": 24,
        "description": "Wikipedia pageview momentum for staple article (per subcategory scope).",
        "formula": "recent_views / prior_views for subcat wiki title",
    },
    {
        "key": "subcat_min_price",
        "name": "Subcategory Min Shelf Price",
        "category": "affordability",
        "source": "internal:price_snapshots",
        "unit": "local",
        "refresh_hours": 8,
        "description": "Cheapest indexed SKU in subcategory across country stores.",
        "formula": "min(price) where name matches subcategory bucket",
    },
    {
        "key": "imf_inflation_yoy",
        "name": "IMF Inflation YoY",
        "category": "macro",
        "source": "external:imf.org",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "IMF DataMapper PCPIPCH — tier-2 cross-check vs World Bank CPI.",
        "formula": "IMF external/datamapper/api/v1/PCPIPCH",
    },
    {
        "key": "eurostat_food_hicp_yoy",
        "name": "Eurostat Food HICP YoY",
        "category": "macro",
        "source": "external:ec.europa.eu/eurostat",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "EU harmonized food inflation (IT, FR) — tier 2.",
        "formula": "PRC_HICP_MANR coicop=CP01 latest annual rate",
    },
    {
        "key": "bcb_food_inflation_mom",
        "name": "BCB Food IPCA MoM",
        "category": "macro",
        "source": "external:bcb.gov.br",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "Brazil central bank food IPCA month-over-month (BR only).",
        "formula": "BCB series 1635 latest month",
    },
    {
        "key": "macro_unemployment_rate",
        "name": "Unemployment Rate",
        "category": "macro",
        "source": "external:worldbank",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "World Bank SL.UEM.TOTL.ZS — labor market tier-2 context.",
        "formula": "World Bank unemployment % of labor force",
    },
    {
        "key": "imf_wb_cpi_gap",
        "name": "IMF vs World Bank CPI Gap",
        "category": "composite",
        "source": "computed",
        "unit": "pp",
        "refresh_hours": 168,
        "description": "IMF inflation minus World Bank official CPI — tier-2 validation spread.",
        "formula": "imf_inflation_yoy - cpi_official_yoy",
    },
    {
        "key": "imf_gdp_growth_yoy",
        "name": "IMF GDP Growth YoY",
        "category": "macro",
        "source": "external:imf.org",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "IMF DataMapper NGDP_RPCH — real GDP growth forecast/outturn.",
        "formula": "IMF NGDP_RPCH latest year",
    },
    {
        "key": "imf_epi_inflation_yoy",
        "name": "IMF End-Period Inflation YoY",
        "category": "macro",
        "source": "external:imf.org",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "IMF PCPIEPCH — CPI inflation end-of-period (tier-2 complement).",
        "formula": "IMF PCPIEPCH latest year",
    },
    {
        "key": "wb_gdp_growth_yoy",
        "name": "World Bank GDP Growth YoY",
        "category": "macro",
        "source": "external:worldbank",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "World Bank NY.GDP.MKTP.KD.ZG — tier-2 growth context.",
        "formula": "World Bank GDP growth annual %",
    },
    {
        "key": "eurostat_headline_hicp_yoy",
        "name": "Eurostat Headline HICP YoY",
        "category": "macro",
        "source": "external:ec.europa.eu/eurostat",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "All-items HICP annual rate (IT, FR) — tier 2.",
        "formula": "PRC_HICP_MANR coicop=CP00",
    },
    {
        "key": "bcb_headline_inflation_mom",
        "name": "BCB Headline IPCA MoM",
        "category": "macro",
        "source": "external:bcb.gov.br",
        "unit": "pct",
        "refresh_hours": 168,
        "description": "Brazil central bank headline IPCA month-over-month (BR only).",
        "formula": "BCB series 433 latest month",
    },
]

ENRICHMENT_INDICATOR_KEYS: tuple[str, ...] = (
    "off_match_rate",
    "off_nutriscore_ab_pct",
    "off_nova_avg",
    "off_ultra_processed_pct",
    "off_ecoscore_avg",
    "wiki_demand_momentum",
    "wiki_staple_momentum",
    "weather_logistics_stress",
    "food_cpi_yoy",
    "food_inflation_spread",
    "staple_price_momentum",
    "imf_inflation_yoy",
    "eurostat_food_hicp_yoy",
    "bcb_food_inflation_mom",
    "macro_unemployment_rate",
    "imf_wb_cpi_gap",
    "imf_gdp_growth_yoy",
    "imf_epi_inflation_yoy",
    "wb_gdp_growth_yoy",
    "eurostat_headline_hicp_yoy",
    "bcb_headline_inflation_mom",
)

TIER2_INDICATOR_KEYS: tuple[str, ...] = (
    "imf_inflation_yoy",
    "eurostat_food_hicp_yoy",
    "eurostat_headline_hicp_yoy",
    "bcb_food_inflation_mom",
    "bcb_headline_inflation_mom",
    "macro_unemployment_rate",
    "imf_wb_cpi_gap",
    "imf_gdp_growth_yoy",
    "imf_epi_inflation_yoy",
    "wb_gdp_growth_yoy",
)

COUNTRY_CURRENCY: dict[str, str] = {
    "PE": "PEN",
    "AR": "ARS",
    "MX": "MXN",
    "CO": "COP",
    "CL": "CLP",
    "BR": "BRL",
    "IT": "EUR",
    "FR": "EUR",
    "US": "USD",
}

WB_COUNTRY: dict[str, str] = {
    "PE": "PE",
    "AR": "AR",
    "MX": "MX",
    "CO": "CO",
    "CL": "CL",
    "BR": "BR",
    "IT": "IT",
    "FR": "FR",
    "US": "US",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _since_iso(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=max(1, hours))).strftime("%Y-%m-%d %H:%M:%S")


def _stores_for_country(country: str | None) -> list[str]:
    if not country:
        return []
    cc = country.upper()
    return [k for k, v in STORES.items() if v.get("country") == cc and not v.get("disabled")]


def seed_indicator_definitions(db) -> None:
    for d in INDICATOR_DEFINITIONS:
        db.execute(
            """
            INSERT INTO indicator_definitions
                (key, name, category, source, unit, refresh_hours, description, formula)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                name=excluded.name,
                category=excluded.category,
                source=excluded.source,
                unit=excluded.unit,
                refresh_hours=excluded.refresh_hours,
                description=excluded.description,
                formula=excluded.formula
            """,
            (
                d["key"],
                d["name"],
                d["category"],
                d["source"],
                d.get("unit", ""),
                d.get("refresh_hours", 24),
                d.get("description", ""),
                d.get("formula", ""),
            ),
        )


def _upsert_indicator_value(
    db,
    *,
    indicator_key: str,
    scope: str,
    value: float | None,
    country: str | None = None,
    line: str | None = None,
    metadata: dict | None = None,
) -> None:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return
    db.execute(
        """
        INSERT INTO indicator_values
            (indicator_key, scope, country, line, value, metadata_json, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            indicator_key,
            scope,
            country or "",
            line or "",
            round(float(value), 4),
            json.dumps(metadata or {}, ensure_ascii=False),
            _now_iso(),
        ),
    )


def _snapshot_filter(country: str | None, line: str | None) -> tuple[str, list]:
    q = " FROM price_snapshots WHERE price > 0"
    params: list = []
    stores = _stores_for_country(country)
    if stores:
        q += f" AND store IN ({','.join('?' * len(stores))})"
        params.extend(stores)
    if line:
        q += " AND line = ?"
        params.append(line)
    return q, params


def compute_promo_intensity(db, country: str | None = None, line: str | None = None) -> float | None:
    filt, params = _snapshot_filter(country, line)
    row = db.execute(
        f"SELECT COUNT(*) AS total, SUM(CASE WHEN discount IS NOT NULL AND discount > 0 THEN 1 ELSE 0 END) AS promos{filt}",
        params,
    ).fetchone()
    total = row["total"] or 0
    if total == 0:
        return None
    return round((row["promos"] or 0) / total * 100, 2)


def compute_price_dispersion(db, country: str | None = None, line: str | None = None) -> float | None:
    filt, params = _snapshot_filter(country, line)
    rows = db.execute(
        f"SELECT LOWER(SUBSTR(name, 1, 40)) AS pname, price{filt} AND name IS NOT NULL",
        params,
    ).fetchall()
    buckets: dict[str, list[float]] = {}
    for r in rows:
        if r["price"] and r["price"] > 0:
            buckets.setdefault(r["pname"], []).append(float(r["price"]))
    cvs: list[float] = []
    for prices in buckets.values():
        if len(prices) < 2:
            continue
        mean = sum(prices) / len(prices)
        if mean <= 0:
            continue
        var = sum((p - mean) ** 2 for p in prices) / len(prices)
        cvs.append(math.sqrt(var) / mean * 100)
    if not cvs:
        return None
    return round(sum(cvs) / len(cvs), 2)


def compute_moat_freshness(db, country: str | None = None, line: str | None = None) -> float | None:
    since = _since_iso(24)
    filt, params = _snapshot_filter(country, line)
    row = db.execute(
        f"SELECT COUNT(*) AS total, SUM(CASE WHEN queried_at >= ? THEN 1 ELSE 0 END) AS fresh{filt}",
        [since, *params],
    ).fetchone()
    total = row["total"] or 0
    if total == 0:
        return None
    return round((row["fresh"] or 0) / total * 100, 2)


def compute_store_coverage(db, country: str | None = None, line: str | None = None) -> float | None:
    filt, params = _snapshot_filter(country, line)
    row = db.execute(f"SELECT COUNT(DISTINCT store) AS n{filt}", params).fetchone()
    n = row["n"] or 0
    return float(n) if n else None


def compute_search_momentum(db, country: str | None = None) -> float | None:
    now = datetime.now(timezone.utc)
    w7 = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    w14 = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
    q_base = "SELECT COUNT(*) AS n FROM search_queries WHERE created_at >= ? AND created_at < ?"
    params_recent: list = [w7, _now_iso()]
    params_prev: list = [w14, w7]
    if country:
        q_base += " AND country = ?"
        params_recent.append(country.upper())
        params_prev.append(country.upper())
    recent = db.execute(q_base, params_recent).fetchone()["n"] or 0
    prev = db.execute(q_base, params_prev).fetchone()["n"] or 0
    if recent == 0 and prev == 0:
        return None
    return round(recent / max(prev, 1), 3)


def compute_basket_stress(db, country: str | None = None) -> float | None:
    """Minimum sum of cheapest canasta item per staple; index vs 30d ago if history exists."""
    stores = _stores_for_country(country)
    if not stores:
        return None
    placeholders = ",".join("?" * len(stores))
    totals: list[float] = []
    for item in CANASTA_ITEMS:
        row = db.execute(
            f"""
            SELECT MIN(price) AS p FROM price_snapshots
            WHERE store IN ({placeholders}) AND price > 0
              AND LOWER(name) LIKE ?
            """,
            [*stores, f"%{item}%"],
        ).fetchone()
        if row and row["p"]:
            totals.append(float(row["p"]))
    if len(totals) < 3:
        return None
    current = sum(totals)
    baseline = current
    try:
        since = _since_iso(24 * 30)
        hist_rows = db.execute(
            f"""
            SELECT SUM(min_p) AS total FROM (
                SELECT product_id, MIN(price) AS min_p
                FROM price_history
                WHERE store IN ({placeholders}) AND recorded_at >= ? AND price > 0
                GROUP BY product_id
            )
            """,
            [*stores, since],
        ).fetchone()
        if hist_rows and hist_rows["total"] and hist_rows["total"] > 0:
            baseline = float(hist_rows["total"])
    except Exception:
        pass
    return round(current / baseline * 100, 2) if baseline > 0 else None


def compute_internal_inflation_avg(db, country: str | None, line: str | None, days: int = 30) -> float | None:
    """Lightweight avg delta_pct from price_history pairs."""
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).strftime("%Y-%m-%d %H:%M:%S")
    stores = _stores_for_country(country)
    q = """
        SELECT product_id, store, price, recorded_at
        FROM price_history
        WHERE price > 0 AND recorded_at >= ?
    """
    params: list = [since]
    if stores:
        q += f" AND store IN ({','.join('?' * len(stores))})"
        params.extend(stores)
    if line:
        q += " AND store IN (SELECT store FROM price_snapshots WHERE line = ? LIMIT 1)"
        params.append(line)
    rows = db.execute(q, params).fetchall()
    series: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        k = f"{r['store']}|{r['product_id']}"
        series.setdefault(k, []).append((r["recorded_at"], float(r["price"])))
    deltas: list[float] = []
    for pts in series.values():
        if len(pts) < 2:
            continue
        pts.sort(key=lambda x: x[0])
        first, last = pts[0][1], pts[-1][1]
        if first > 0:
            deltas.append((last - first) / first * 100)
    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 2)


def compute_staple_price_momentum(db, country: str | None, days: int = 7) -> float | None:
    """Average % price change for canasta staples over the last N days."""
    stores = _stores_for_country(country)
    if not stores:
        return None
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).strftime("%Y-%m-%d %H:%M:%S")
    ph = ",".join("?" * len(stores))
    like_clauses = " OR ".join(["LOWER(ps.name) LIKE ?"] * len(CANASTA_ITEMS))
    rows = db.execute(
        f"""
        SELECT ph.product_id, ph.store, ph.price, ph.recorded_at
        FROM price_history ph
        INNER JOIN price_snapshots ps ON ps.product_id = ph.product_id AND ps.store = ph.store
        WHERE ph.store IN ({ph}) AND ph.price > 0 AND ph.recorded_at >= ?
          AND ({like_clauses})
        """,
        [*stores, since, *[f"%{item}%" for item in CANASTA_ITEMS]],
    ).fetchall()
    series: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        k = f"{r['store']}|{r['product_id']}"
        series.setdefault(k, []).append((r["recorded_at"], float(r["price"])))
    deltas: list[float] = []
    for pts in series.values():
        if len(pts) < 2:
            continue
        pts.sort(key=lambda x: x[0])
        first, last = pts[0][1], pts[-1][1]
        if first > 0:
            deltas.append((last - first) / first * 100)
    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 2)


def fetch_fx_rates() -> dict[str, float]:
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get("https://open.er-api.com/v6/latest/USD")
            r.raise_for_status()
            data = r.json()
            if data.get("result") != "success":
                return {}
            rates = data.get("rates") or {}
            return {k: float(v) for k, v in rates.items() if v}
    except Exception as e:
        logger.warning("FX fetch failed: %s", e)
        return {}


def fetch_worldbank_indicator(country_code: str, indicator: str) -> float | None:
    wb = WB_COUNTRY.get(country_code.upper())
    if not wb:
        return None
    url = (
        f"https://api.worldbank.org/v2/country/{wb}/indicator/{indicator}"
        f"?format=json&per_page=5&date={(datetime.now().year - 5)}:{datetime.now().year}"
    )
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.get(url)
            r.raise_for_status()
            payload = r.json()
            if not isinstance(payload, list) or len(payload) < 2:
                return None
            for entry in payload[1]:
                val = entry.get("value")
                if val is not None:
                    return round(float(val), 3)
    except Exception as e:
        logger.warning("World Bank fetch failed (%s/%s): %s", country_code, indicator, e)
    return None


def refresh_internal_indicators(db, country: str | None = None, line: str | None = None) -> int:
    scope = f"{country or 'global'}:{line or 'all'}"
    writers = [
        ("promo_intensity", compute_promo_intensity(db, country, line)),
        ("price_dispersion", compute_price_dispersion(db, country, line)),
        ("moat_freshness", compute_moat_freshness(db, country, line)),
        ("store_coverage", compute_store_coverage(db, country, line)),
        ("search_momentum", compute_search_momentum(db, country)),
        ("basket_stress_index", compute_basket_stress(db, country)),
    ]
    n = 0
    for key, val in writers:
        if val is not None:
            _upsert_indicator_value(db, indicator_key=key, scope=scope, value=val, country=country, line=line)
            n += 1
    return n


def _indicator_is_stale(db, indicator_key: str, scope: str, refresh_hours: int) -> bool:
    row = db.execute(
        """
        SELECT recorded_at FROM indicator_values
        WHERE indicator_key = ? AND scope = ?
        ORDER BY recorded_at DESC LIMIT 1
        """,
        (indicator_key, scope),
    ).fetchone()
    if not row or not row["recorded_at"]:
        return True
    try:
        raw = str(row["recorded_at"]).replace("T", " ")[:19]
        recorded = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    age = datetime.now(timezone.utc) - recorded
    return age >= timedelta(hours=max(1, refresh_hours))


def refresh_external_indicators(db, country: str | None = None) -> int:
    n = 0
    cc = (country or "PE").upper()
    scope = f"{cc}:macro"

    defs = {d["key"]: d for d in INDICATOR_DEFINITIONS}

    ccy = COUNTRY_CURRENCY.get(cc)
    fx_hours = defs.get("fx_usd_local", {}).get("refresh_hours", 24)
    if ccy and _indicator_is_stale(db, "fx_usd_local", scope, fx_hours):
        rates = fetch_fx_rates()
        if ccy in rates:
            _upsert_indicator_value(
                db,
                indicator_key="fx_usd_local",
                scope=scope,
                value=rates[ccy],
                country=cc,
                metadata={"currency": ccy, "base": "USD"},
            )
            n += 1

    cpi = None
    cpi_hours = defs.get("cpi_official_yoy", {}).get("refresh_hours", 168)
    if _indicator_is_stale(db, "cpi_official_yoy", scope, cpi_hours):
        cpi = fetch_worldbank_indicator(cc, "FP.CPI.TOTL.ZG")
        if cpi is not None:
            _upsert_indicator_value(db, indicator_key="cpi_official_yoy", scope=scope, value=cpi, country=cc)
            n += 1
    else:
        row = db.execute(
            "SELECT value FROM indicator_values WHERE indicator_key = ? AND scope = ? ORDER BY recorded_at DESC LIMIT 1",
            ("cpi_official_yoy", scope),
        ).fetchone()
        if row and row["value"] is not None:
            cpi = float(row["value"])

    food_hours = defs.get("food_price_index", {}).get("refresh_hours", 168)
    if _indicator_is_stale(db, "food_price_index", scope, food_hours):
        food = fetch_worldbank_indicator(cc, "AG.PRD.FOOD.XD")
        if food is not None:
            _upsert_indicator_value(db, indicator_key="food_price_index", scope=scope, value=food, country=cc)
            n += 1

    internal_inf = compute_internal_inflation_avg(db, cc, None, days=30)
    if internal_inf is not None and cpi is not None:
        _upsert_indicator_value(
            db,
            indicator_key="collector_vs_official_gap",
            scope=scope,
            value=round(internal_inf - cpi, 2),
            country=cc,
            metadata={"internal_inflation_pct": internal_inf, "official_cpi_pct": cpi},
        )
        n += 1

    return n


def refresh_enrichment_indicators(db, country: str | None = None) -> int:
    """Open Food Facts sample + Wikimedia + Open-Meteo + food CPI."""
    if os.getenv("ENRICHMENT_AUTO_REFRESH", "1").strip() in ("0", "false", "no"):
        return 0

    from market_enrich_sources import (
        fetch_food_cpi_yoy,
        fetch_weather_logistics_stress,
        fetch_wiki_demand_momentum,
        fetch_wiki_staple_momentum,
        sample_off_coverage,
    )

    cc = (country or "PE").upper()
    scope = f"{cc}:enrichment"
    defs = {d["key"]: d for d in INDICATOR_DEFINITIONS}
    n = 0

    off_hours = defs.get("off_match_rate", {}).get("refresh_hours", 168)
    if _indicator_is_stale(db, "off_match_rate", scope, off_hours):
        off = sample_off_coverage(db, cc)
        if off.get("match_rate_pct") is not None:
            _upsert_indicator_value(
                db,
                indicator_key="off_match_rate",
                scope=scope,
                value=off["match_rate_pct"],
                country=cc,
                metadata={"sampled": off["sampled"], "matched": off["matched"], "samples": off.get("samples", [])},
            )
            n += 1
        if off.get("nutriscore_ab_pct") is not None:
            _upsert_indicator_value(
                db,
                indicator_key="off_nutriscore_ab_pct",
                scope=scope,
                value=off["nutriscore_ab_pct"],
                country=cc,
            )
            n += 1
        if off.get("nova_avg") is not None:
            _upsert_indicator_value(
                db,
                indicator_key="off_nova_avg",
                scope=scope,
                value=off["nova_avg"],
                country=cc,
            )
            n += 1
        if off.get("ultra_processed_pct") is not None:
            _upsert_indicator_value(
                db,
                indicator_key="off_ultra_processed_pct",
                scope=scope,
                value=off["ultra_processed_pct"],
                country=cc,
            )
            n += 1
        if off.get("ecoscore_avg") is not None:
            _upsert_indicator_value(
                db,
                indicator_key="off_ecoscore_avg",
                scope=scope,
                value=off["ecoscore_avg"],
                country=cc,
            )
            n += 1

    wiki_hours = defs.get("wiki_demand_momentum", {}).get("refresh_hours", 24)
    if _indicator_is_stale(db, "wiki_demand_momentum", scope, wiki_hours):
        wiki = fetch_wiki_demand_momentum(cc)
        if wiki is not None:
            _upsert_indicator_value(
                db, indicator_key="wiki_demand_momentum", scope=scope, value=wiki, country=cc
            )
            n += 1

    wiki_staple_hours = defs.get("wiki_staple_momentum", {}).get("refresh_hours", 24)
    if _indicator_is_stale(db, "wiki_staple_momentum", scope, wiki_staple_hours):
        wiki_staple = fetch_wiki_staple_momentum(cc)
        if wiki_staple is not None:
            _upsert_indicator_value(
                db, indicator_key="wiki_staple_momentum", scope=scope, value=wiki_staple, country=cc
            )
            n += 1

    staple_hours = defs.get("staple_price_momentum", {}).get("refresh_hours", 8)
    if _indicator_is_stale(db, "staple_price_momentum", scope, staple_hours):
        staple_mom = compute_staple_price_momentum(db, cc)
        if staple_mom is not None:
            _upsert_indicator_value(
                db,
                indicator_key="staple_price_momentum",
                scope=scope,
                value=staple_mom,
                country=cc,
                metadata={"window_days": 7, "staples": CANASTA_ITEMS[:5]},
            )
            n += 1

    weather_hours = defs.get("weather_logistics_stress", {}).get("refresh_hours", 12)
    if _indicator_is_stale(db, "weather_logistics_stress", scope, weather_hours):
        weather = fetch_weather_logistics_stress(cc)
        if weather is not None:
            _upsert_indicator_value(
                db, indicator_key="weather_logistics_stress", scope=scope, value=weather, country=cc
            )
            n += 1

    food_cpi_hours = defs.get("food_cpi_yoy", {}).get("refresh_hours", 168)
    food_cpi_val: float | None = None
    if _indicator_is_stale(db, "food_cpi_yoy", scope, food_cpi_hours):
        food_cpi = fetch_food_cpi_yoy(cc)
        if food_cpi is not None:
            food_cpi_val = food_cpi
            _upsert_indicator_value(
                db, indicator_key="food_cpi_yoy", scope=scope, value=food_cpi, country=cc
            )
            n += 1
    else:
        row = db.execute(
            """
            SELECT value FROM indicator_values
            WHERE indicator_key = 'food_cpi_yoy' AND scope = ?
            ORDER BY recorded_at DESC LIMIT 1
            """,
            (scope,),
        ).fetchone()
        if row and row["value"] is not None:
            food_cpi_val = float(row["value"])

    spread_hours = defs.get("food_inflation_spread", {}).get("refresh_hours", 168)
    if _indicator_is_stale(db, "food_inflation_spread", scope, spread_hours) and food_cpi_val is not None:
        macro_scope = f"{cc}:macro"
        cpi_row = db.execute(
            """
            SELECT value FROM indicator_values
            WHERE indicator_key = 'cpi_official_yoy' AND scope = ?
            ORDER BY recorded_at DESC LIMIT 1
            """,
            (macro_scope,),
        ).fetchone()
        if cpi_row and cpi_row["value"] is not None:
            spread = round(food_cpi_val - float(cpi_row["value"]), 3)
            _upsert_indicator_value(
                db,
                indicator_key="food_inflation_spread",
                scope=scope,
                value=spread,
                country=cc,
                metadata={"food_cpi_yoy": food_cpi_val, "cpi_official_yoy": float(cpi_row["value"])},
            )
            n += 1

    n += _refresh_tier2_indicators(db, cc, scope, defs)

    if os.getenv("SUBCATEGORY_AUTO_REFRESH", "1").strip() not in ("0", "false", "no"):
        from market_enrich_subcategory import refresh_subcategory_enrichment

        n += refresh_subcategory_enrichment(db, cc, _upsert_indicator_value)

    return n


def _refresh_tier2_indicators(db, cc: str, scope: str, defs: dict) -> int:
    if os.getenv("TIER2_AUTO_REFRESH", "1").strip() in ("0", "false", "no"):
        return 0

    from market_enrich_sources import (
        fetch_bcb_food_inflation_mom,
        fetch_bcb_headline_inflation_mom,
        fetch_eurostat_food_hicp_yoy,
        fetch_eurostat_headline_hicp_yoy,
        fetch_imf_epi_inflation_yoy,
        fetch_imf_gdp_growth_yoy,
        fetch_imf_inflation_yoy,
        fetch_wb_gdp_growth_yoy,
        fetch_wb_unemployment_rate,
    )

    n = 0
    imf_val: float | None = None

    imf_hours = defs.get("imf_inflation_yoy", {}).get("refresh_hours", 168)
    if _indicator_is_stale(db, "imf_inflation_yoy", scope, imf_hours):
        imf = fetch_imf_inflation_yoy(cc)
        if imf is not None:
            imf_val = imf
            _upsert_indicator_value(
                db, indicator_key="imf_inflation_yoy", scope=scope, value=imf, country=cc
            )
            n += 1
    else:
        row = db.execute(
            "SELECT value FROM indicator_values WHERE indicator_key = 'imf_inflation_yoy' AND scope = ? ORDER BY recorded_at DESC LIMIT 1",
            (scope,),
        ).fetchone()
        if row and row["value"] is not None:
            imf_val = float(row["value"])

    euro_hours = defs.get("eurostat_food_hicp_yoy", {}).get("refresh_hours", 168)
    if _indicator_is_stale(db, "eurostat_food_hicp_yoy", scope, euro_hours):
        euro = fetch_eurostat_food_hicp_yoy(cc)
        if euro is not None:
            _upsert_indicator_value(
                db, indicator_key="eurostat_food_hicp_yoy", scope=scope, value=euro, country=cc
            )
            n += 1

    bcb_hours = defs.get("bcb_food_inflation_mom", {}).get("refresh_hours", 168)
    if _indicator_is_stale(db, "bcb_food_inflation_mom", scope, bcb_hours):
        bcb = fetch_bcb_food_inflation_mom(cc)
        if bcb is not None:
            _upsert_indicator_value(
                db, indicator_key="bcb_food_inflation_mom", scope=scope, value=bcb, country=cc
            )
            n += 1

    unemp_hours = defs.get("macro_unemployment_rate", {}).get("refresh_hours", 168)
    if _indicator_is_stale(db, "macro_unemployment_rate", scope, unemp_hours):
        unemp = fetch_wb_unemployment_rate(cc)
        if unemp is not None:
            _upsert_indicator_value(
                db, indicator_key="macro_unemployment_rate", scope=scope, value=unemp, country=cc
            )
            n += 1

    gap_hours = defs.get("imf_wb_cpi_gap", {}).get("refresh_hours", 168)
    if _indicator_is_stale(db, "imf_wb_cpi_gap", scope, gap_hours) and imf_val is not None:
        macro_scope = f"{cc}:macro"
        cpi_row = db.execute(
            "SELECT value FROM indicator_values WHERE indicator_key = 'cpi_official_yoy' AND scope = ? ORDER BY recorded_at DESC LIMIT 1",
            (macro_scope,),
        ).fetchone()
        if cpi_row and cpi_row["value"] is not None:
            gap = round(imf_val - float(cpi_row["value"]), 3)
            _upsert_indicator_value(
                db,
                indicator_key="imf_wb_cpi_gap",
                scope=scope,
                value=gap,
                country=cc,
                metadata={"imf_inflation_yoy": imf_val, "cpi_official_yoy": float(cpi_row["value"])},
            )
            n += 1

    for key, fetcher in (
        ("imf_gdp_growth_yoy", fetch_imf_gdp_growth_yoy),
        ("imf_epi_inflation_yoy", fetch_imf_epi_inflation_yoy),
        ("wb_gdp_growth_yoy", fetch_wb_gdp_growth_yoy),
        ("eurostat_headline_hicp_yoy", fetch_eurostat_headline_hicp_yoy),
        ("bcb_headline_inflation_mom", fetch_bcb_headline_inflation_mom),
    ):
        hours = defs.get(key, {}).get("refresh_hours", 168)
        if _indicator_is_stale(db, key, scope, hours):
            val = fetcher(cc)
            if val is not None:
                _upsert_indicator_value(db, indicator_key=key, scope=scope, value=val, country=cc)
                n += 1

    return n


def refresh_enrichment_only(country: str | None = None) -> dict[str, Any]:
    """Refresh only enrichment indicators (OFF, Wiki, weather, food CPI)."""
    db = get_db()
    seed_indicator_definitions(db)
    written = refresh_enrichment_indicators(db, country)
    db.commit()
    db.close()
    return {"status": "ok", "enrichment_written": written, "country": country}


def refresh_indicators(country: str | None = None, line: str | None = None) -> dict[str, int]:
    db = get_db()
    seed_indicator_definitions(db)
    internal = refresh_internal_indicators(db, country, line)
    external = refresh_external_indicators(db, country)
    enrichment = refresh_enrichment_indicators(db, country)
    db.commit()
    db.close()
    return {
        "internal_written": internal,
        "external_written": external,
        "enrichment_written": enrichment,
    }


def refresh_after_collection(countries: list[str] | None = None) -> dict[str, Any]:
    """Run after each collector cycle. Enabled by default (INDICATOR_AUTO_REFRESH=1)."""
    if os.getenv("INDICATOR_AUTO_REFRESH", "1").strip() in ("0", "false", "no"):
        return {"skipped": True, "reason": "INDICATOR_AUTO_REFRESH disabled"}

    if not countries:
        countries = sorted(
            {v["country"] for v in STORES.values() if not v.get("disabled") and v.get("country")}
        )

    summary: dict[str, Any] = {
        "skipped": False,
        "countries": countries,
        "per_country": {},
        "internal_written": 0,
        "external_written": 0,
        "enrichment_written": 0,
    }
    for cc in countries:
        result = refresh_indicators(country=cc, line=None)
        summary["per_country"][cc] = result
        summary["internal_written"] += result["internal_written"]
        summary["external_written"] += result["external_written"]
        summary["enrichment_written"] += result.get("enrichment_written", 0)
    return summary


def get_indicator_catalog() -> list[dict]:
    return list(INDICATOR_DEFINITIONS)


def get_latest_values(
    db,
    indicator_key: str | None = None,
    country: str | None = None,
    line: str | None = None,
    limit: int = 50,
) -> list[dict]:
    q = """
        SELECT iv.indicator_key, iv.scope, iv.country, iv.line, iv.value,
               iv.metadata_json, iv.recorded_at, id.name, id.category, id.unit, id.source
        FROM indicator_values iv
        LEFT JOIN indicator_definitions id ON id.key = iv.indicator_key
        WHERE 1=1
    """
    params: list = []
    if indicator_key:
        q += " AND iv.indicator_key = ?"
        params.append(indicator_key)
    if country:
        q += " AND (iv.country = ? OR iv.country = '')"
        params.append(country.upper())
    if line:
        q += " AND (iv.line = ? OR iv.line = '')"
        params.append(line)
    q += " ORDER BY iv.recorded_at DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(q, params).fetchall()
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        dedupe = f"{r['indicator_key']}|{r['scope']}|{r['country']}|{r['line']}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        meta = {}
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except json.JSONDecodeError:
            pass
        out.append(
            {
                "key": r["indicator_key"],
                "name": r["name"],
                "category": r["category"],
                "source": r["source"],
                "unit": r["unit"],
                "scope": r["scope"],
                "country": r["country"] or None,
                "line": r["line"] or None,
                "value": r["value"],
                "metadata": meta,
                "recorded_at": r["recorded_at"],
            }
        )
    return out


def compute_composite_scores(country: str | None = None, line: str | None = None) -> dict[str, Any]:
    db = get_db()
    seed_indicator_definitions(db)
    refresh_internal_indicators(db, country, line)
    db.commit()

    latest = {v["key"]: v for v in get_latest_values(db, country=country, line=line, limit=200)}

    promo = latest.get("promo_intensity", {}).get("value")
    dispersion = latest.get("price_dispersion", {}).get("value")
    freshness = latest.get("moat_freshness", {}).get("value")
    basket = latest.get("basket_stress_index", {}).get("value")
    gap = latest.get("collector_vs_official_gap", {}).get("value")
    off_match = latest.get("off_match_rate", {}).get("value")
    off_nova = latest.get("off_nova_avg", {}).get("value")
    off_nutri_ab = latest.get("off_nutriscore_ab_pct", {}).get("value")
    off_ultra = latest.get("off_ultra_processed_pct", {}).get("value")
    off_eco = latest.get("off_ecoscore_avg", {}).get("value")
    wiki = latest.get("wiki_demand_momentum", {}).get("value")
    wiki_staple = latest.get("wiki_staple_momentum", {}).get("value")
    weather = latest.get("weather_logistics_stress", {}).get("value")
    food_spread = latest.get("food_inflation_spread", {}).get("value")
    staple_mom = latest.get("staple_price_momentum", {}).get("value")
    imf_gap = latest.get("imf_wb_cpi_gap", {}).get("value")
    unemployment = latest.get("macro_unemployment_rate", {}).get("value")
    imf_gdp = latest.get("imf_gdp_growth_yoy", {}).get("value")

    scores: dict[str, Any] = {}

    if promo is not None:
        scores["retail_aggression"] = {
            "score": round(min(promo * 2, 100), 1),
            "label": "high" if promo > 25 else "moderate" if promo > 10 else "low",
            "input": {"promo_intensity_pct": promo},
        }

    if dispersion is not None:
        scores["price_fairness"] = {
            "score": round(max(0, 100 - dispersion), 1),
            "label": "competitive" if dispersion > 15 else "stable",
            "input": {"price_dispersion_pct": dispersion},
        }

    if basket is not None:
        scores["basket_stress"] = {
            "score": round(basket, 1),
            "label": "elevated" if basket > 105 else "normal" if basket > 95 else "eased",
            "input": {"basket_stress_index": basket},
        }

    if freshness is not None:
        scores["data_confidence"] = {
            "score": round(freshness, 1),
            "label": "fresh" if freshness >= 80 else "stale",
            "input": {"moat_freshness_pct": freshness},
        }

    if gap is not None:
        scores["macro_alignment"] = {
            "score": round(max(0, 100 - abs(gap) * 5), 1),
            "label": "aligned" if abs(gap) < 5 else "divergent",
            "input": {"collector_vs_official_gap_pp": gap},
        }

    if off_match is not None:
        nova_penalty = max(0, (off_nova or 3) - 2) * 15 if off_nova is not None else 0
        scores["product_intelligence"] = {
            "score": round(max(0, min(100, off_match - nova_penalty)), 1),
            "label": "rich" if off_match > 50 else "sparse",
            "input": {"off_match_rate_pct": off_match, "off_nova_avg": off_nova},
        }

    if wiki is not None:
        scores["demand_outlook"] = {
            "score": round(min(wiki * 50, 100), 1),
            "label": "rising" if wiki > 1.1 else "cooling" if wiki < 0.9 else "stable",
            "input": {"wiki_demand_momentum": wiki},
        }

    if weather is not None:
        scores["logistics_risk"] = {
            "score": round(weather, 1),
            "label": "elevated" if weather > 40 else "normal",
            "input": {"weather_logistics_stress": weather},
        }

    if food_spread is not None:
        scores["food_premium"] = {
            "score": round(max(0, min(100, 50 + food_spread * 5)), 1),
            "label": "elevated" if food_spread > 2 else "normal" if food_spread > 0 else "eased",
            "input": {"food_inflation_spread_pp": food_spread},
        }

    if off_nutri_ab is not None or off_ultra is not None or off_eco is not None:
        nutri_part = off_nutri_ab or 0
        ultra_penalty = (off_ultra or 0) * 0.4
        eco_part = ((off_eco or 3) / 5) * 100 if off_eco is not None else 50
        scores["nutrition_quality"] = {
            "score": round(max(0, min(100, nutri_part * 0.5 + eco_part * 0.3 - ultra_penalty + 10)), 1),
            "label": "healthy" if (off_nutri_ab or 0) > 40 and (off_ultra or 100) < 30 else "mixed",
            "input": {
                "off_nutriscore_ab_pct": off_nutri_ab,
                "off_ultra_processed_pct": off_ultra,
                "off_ecoscore_avg": off_eco,
            },
        }

    if wiki_staple is not None:
        scores["staple_demand"] = {
            "score": round(min(wiki_staple * 50, 100), 1),
            "label": "rising" if wiki_staple > 1.1 else "cooling" if wiki_staple < 0.9 else "stable",
            "input": {"wiki_staple_momentum": wiki_staple, "staple_price_momentum_pct": staple_mom},
        }

    if imf_gap is not None:
        scores["macro_validation"] = {
            "score": round(max(0, 100 - abs(imf_gap) * 8), 1),
            "label": "consistent" if abs(imf_gap) < 2 else "divergent",
            "input": {"imf_wb_cpi_gap_pp": imf_gap},
        }

    if unemployment is not None:
        scores["labor_stress"] = {
            "score": round(min(unemployment * 8, 100), 1),
            "label": "elevated" if unemployment > 8 else "normal",
            "input": {"macro_unemployment_rate_pct": unemployment},
        }

    if imf_gdp is not None:
        scores["growth_outlook"] = {
            "score": round(max(0, min(100, 50 + imf_gdp * 5)), 1),
            "label": "expanding" if imf_gdp > 3 else "slow" if imf_gdp > 0 else "contracting",
            "input": {"imf_gdp_growth_yoy_pct": imf_gdp},
        }

    db.close()
    return {
        "country": country,
        "line": line,
        "computed_at": _now_iso(),
        "scores": scores,
        "disclaimer": "Composite scores combine internal moat signals with public macro APIs where available.",
    }
