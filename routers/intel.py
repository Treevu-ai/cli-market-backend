"""Intel / inflation tracking — the data-moat-as-product angle.

Endpoints:
  GET /v1/intel/inflation       Per-product price delta over the last N days
  GET /v1/intel/alerts          Price movers vs threshold (from price_history)
  GET /v1/intel/indicators      Indicator catalog (public API + internal moat)
  GET /v1/intel/indicators/{key} Latest values for one indicator
  GET /v1/intel/scores          Composite moat scores
  GET /v1/intel/basket-stress   Canasta affordability signal
  POST /v1/intel/refresh              Recompute and fetch external indicators
  GET  /v1/intel/enrichment           Latest enrichment indicators
  GET  /v1/intel/enrichment/subcategories  Per-staple enrichment (leche, arroz, …)
  POST /v1/intel/enrichment/refresh   Refresh enrichment indicators only
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from market_core import STORES, get_db
from market_enrich_subcategory import ENRICH_SUBCATEGORIES, get_subcategory_enrichment
from market_indicators import (
    ENRICHMENT_INDICATOR_KEYS,
    TIER2_INDICATOR_KEYS,
    compute_basket_stress,
    compute_composite_scores,
    get_indicator_catalog,
    get_latest_values,
    refresh_enrichment_only,
    refresh_indicators,
)

router = APIRouter(tags=["intel"])


def _since_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(1, days))).strftime("%Y-%m-%d %H:%M:%S")


@router.get("/v1/intel/inflation")
def inflation_tracker(
    country: str | None = None,
    line: str | None = None,
    days: int = 30,
    limit: int = 100,
):
    """Compute per-product price deltas within the last `days` window.

    Compares earliest vs latest snapshot per product name in the window.
    Intended for agent-facing inflation signals — not official CPI indices.
    """
    db = get_db()
    since = _since_iso(days)
    q = (
        "SELECT name, store, store_name, currency, price, queried_at "
        "FROM price_snapshots WHERE price > 0 AND queried_at >= ?"
    )
    params: list = [since]
    if country:
        cc_stores = [k for k, v in STORES.items() if v["country"] == country.upper() and not v.get("disabled")]
        if cc_stores:
            q += f" AND store IN ({','.join('?' * len(cc_stores))})"
            params.extend(cc_stores)
    if line:
        q += " AND line = ?"
        params.append(line)
    q += " ORDER BY queried_at DESC LIMIT ?"
    params.append(limit * 4)
    rows = db.execute(q, params).fetchall()
    db.close()

    prods: dict[str, list[dict]] = {}
    for r in rows:
        k = f"{r['store']}|{r['name'].lower()[:40]}"
        prods.setdefault(k, []).append(
            {
                "price": r["price"],
                "date": r["queried_at"],
                "store": r["store_name"],
                "currency": r["currency"],
            }
        )

    items: list[dict] = []
    for _key, snaps in prods.items():
        snaps.sort(key=lambda s: s["date"])
        if len(snaps) >= 2:
            first = snaps[0]
            last = snaps[-1]
            if first["price"] > 0:
                d = round(last["price"] - first["price"], 2)
                dp = round((d / first["price"]) * 100, 1)
                items.append(
                    {
                        "product": _key.split("|", 1)[1],
                        "first_price": first["price"],
                        "last_price": last["price"],
                        "first_date": first["date"],
                        "last_date": last["date"],
                        "delta": d,
                        "delta_pct": dp,
                        "currency": first["currency"],
                        "store": first["store"],
                    }
                )
    items.sort(key=lambda x: abs(x["delta_pct"]), reverse=True)
    items = items[:limit]
    avg = round(sum(i["delta_pct"] for i in items) / len(items), 1) if items else 0
    return {
        "country": country,
        "line": line,
        "days": days,
        "since": since,
        "products_tracked": len(items),
        "avg_inflation_pct": avg,
        "items": items,
        "disclaimer": "Internal collector signal — not an official inflation index.",
    }


@router.get("/v1/intel/alerts")
def intel_alerts(
    product: str,
    store: str | None = None,
    threshold_pct: float = 5.0,
    limit: int = 10,
):
    """Price alerts from price_history when delta exceeds threshold_pct."""
    db = get_db()
    since = _since_iso(30)
    q = """
        SELECT ph.product_id, ph.store, ph.price, ph.recorded_at, ps.name, ps.store_name, ps.currency
        FROM price_history ph
        LEFT JOIN price_snapshots ps ON ps.product_id = ph.product_id AND ps.store = ph.store
        WHERE ph.recorded_at >= ? AND ph.price > 0
          AND LOWER(COALESCE(ps.name, '')) LIKE ?
    """
    params: list = [since, f"%{product.lower()}%"]
    if store:
        q += " AND ph.store = ?"
        params.append(store)
    q += " ORDER BY ph.recorded_at DESC LIMIT ?"
    params.append(limit * 20)
    rows = db.execute(q, params).fetchall()
    db.close()

    series: dict[str, list] = {}
    for r in rows:
        k = f"{r['store']}|{r['product_id']}"
        series.setdefault(k, []).append(r)

    alerts: list[dict] = []
    for _key, pts in series.items():
        if len(pts) < 2:
            continue
        pts.sort(key=lambda x: x["recorded_at"])
        first, last = pts[0], pts[-1]
        if not first["price"] or first["price"] <= 0:
            continue
        dp = round((float(last["price"]) - float(first["price"])) / float(first["price"]) * 100, 1)
        if abs(dp) >= threshold_pct:
            alerts.append(
                {
                    "product_id": last["product_id"],
                    "product": last["name"] or product,
                    "store": last["store"],
                    "store_name": last["store_name"],
                    "currency": last["currency"],
                    "first_price": first["price"],
                    "last_price": last["price"],
                    "delta_pct": dp,
                    "direction": "up" if dp > 0 else "down",
                }
            )
    alerts.sort(key=lambda x: abs(x["delta_pct"]), reverse=True)
    return {
        "product": product,
        "store": store,
        "threshold_pct": threshold_pct,
        "alerts": alerts[:limit],
        "message": f"{len(alerts[:limit])} alert(s) above {threshold_pct}% threshold.",
    }


@router.get("/v1/intel/indicators")
def list_indicators():
    """Catalog of moat indicators (internal, external public APIs, composite)."""
    return {
        "count": len(get_indicator_catalog()),
        "indicators": get_indicator_catalog(),
    }


@router.get("/v1/intel/indicators/{indicator_key}")
def get_indicator(
    indicator_key: str,
    country: str | None = None,
    line: str | None = None,
    limit: int = 30,
):
    """Latest time-series points for one indicator."""
    db = get_db()
    values = get_latest_values(db, indicator_key=indicator_key, country=country, line=line, limit=limit)
    db.close()
    meta = next((i for i in get_indicator_catalog() if i["key"] == indicator_key), None)
    return {
        "key": indicator_key,
        "definition": meta,
        "country": country,
        "line": line,
        "values": values,
    }


@router.get("/v1/intel/scores")
def intel_scores(country: str | None = None, line: str | None = None):
    """Composite scores blending moat signals and public macro data."""
    return compute_composite_scores(country=country, line=line)


@router.get("/v1/intel/basket-stress")
def basket_stress(country: str | None = None):
    """Minimum canasta básica stress index for a country."""
    db = get_db()
    value = compute_basket_stress(db, country)
    db.close()
    return {
        "country": country,
        "basket_stress_index": value,
        "interpretation": (
            "elevated (>105)" if value and value > 105
            else "eased (<95)" if value and value < 95
            else "normal"
        ),
        "disclaimer": "Based on cheapest indexed staple per item — not official CPI basket.",
    }


@router.post("/v1/intel/refresh")
def intel_refresh(country: str | None = None, line: str | None = None):
    """Refresh internal computed indicators and fetch public API macro signals."""
    result = refresh_indicators(country=country, line=line)
    return {"status": "ok", **result}


@router.get("/v1/intel/enrichment")
def intel_enrichment(country: str | None = None, limit: int = 20):
    """Latest enrichment indicators (OFF, Wikimedia, weather, food CPI) for a country."""
    db = get_db()
    keys = ENRICHMENT_INDICATOR_KEYS
    values = get_latest_values(db, country=country, limit=limit * 3)
    enriched = [v for v in values if v.get("key") in keys]
    db.close()
    return {
        "country": country,
        "count": len(enriched),
        "indicators": enriched,
        "sources": [
            "openfoodfacts",
            "wikimedia",
            "open-meteo.com",
            "worldbank",
            "imf.org",
            "eurostat",
            "bcb.gov.br",
        ],
        "tier2_keys": list(TIER2_INDICATOR_KEYS),
    }


@router.get("/v1/intel/enrichment/subcategories")
def intel_enrichment_subcategories(country: str = "PE"):
    """Per-subcategory signals: price momentum, wiki demand, min shelf price."""
    db = get_db()
    items = get_subcategory_enrichment(db, country)
    db.close()
    return {
        "country": country.upper(),
        "subcategories": ENRICH_SUBCATEGORIES,
        "count": len(items),
        "items": items,
    }


@router.post("/v1/intel/enrichment/refresh")
def intel_enrichment_refresh(country: str | None = None):
    """Refresh only enrichment indicators (OFF sample, Wiki, weather, food CPI)."""
    return refresh_enrichment_only(country=country)
