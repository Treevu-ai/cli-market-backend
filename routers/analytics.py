"""Read-only analytics over price_snapshots.

Endpoints:
  GET /analytics/price-history   Snapshots filtered by product/store/line
  GET /analytics/stats           Totals + last snapshot timestamp
  GET /analytics/trending        Recent products (placeholder — sorted by queried_at)
  GET /analytics/brands          Top brands by snapshot count
  GET /analytics/indicators      Latest moat indicator values
  GET /v1/brand-monitor          Cross-store SKU snapshot for a brand + competitors
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header

from market_core import STORES
from market_indicators import get_indicator_catalog, get_latest_values
from server_deps import get_db_dep, require_api_key
from index_gate import enrich_list

router = APIRouter(tags=["analytics"])


def _country_stores(country: str | None) -> list[str] | None:
    """Store keys for a country, or None when no country filter was requested."""
    if not country:
        return None
    return [
        s for s, sv in STORES.items()
        if sv.get("country", "").upper() == country.upper() and not sv.get("disabled")
    ]


def _since_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(1, days))).strftime("%Y-%m-%d %H:%M:%S")


@router.get("/analytics/price-history")
def price_history(
    product_id: str | None = None,
    store: str | None = None,
    line: str | None = None,
    limit: int = 50,
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    require_api_key(authorization)
    q = "SELECT * FROM price_snapshots WHERE 1=1"
    params: list = []
    if product_id:
        q += " AND product_id = ?"
        params.append(product_id)
    if store:
        q += " AND store = ?"
        params.append(store)
    if line:
        q += " AND line = ?"
        params.append(line)
    q += " ORDER BY queried_at DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(q, params).fetchall()
    snapshots = [dict(r) for r in rows]
    enrich_list(snapshots)
    return {"count": len(snapshots), "snapshots": snapshots}


@router.get("/analytics/stats")
def analytics_stats(authorization: str | None = Header(None), db = Depends(get_db_dep)):
    require_api_key(authorization)
    snap = db.execute(
        """SELECT COUNT(*) AS total_snapshots,
                  COUNT(DISTINCT store) AS stores_tracked,
                  COUNT(DISTINCT product_id) AS products_tracked,
                  MAX(queried_at) AS latest
           FROM price_snapshots"""
    ).fetchone()
    total_queries = db.execute("SELECT COUNT(*) as n FROM search_queries").fetchone()["n"]
    return {
        "total_price_snapshots": snap["total_snapshots"],
        "total_search_queries": total_queries,
        "unique_stores_tracked": snap["stores_tracked"],
        "unique_products_tracked": snap["products_tracked"],
        "latest_snapshot_at": snap["latest"],
    }


@router.get("/analytics/trending")
def analytics_trending(country: str | None = None, line: str | None = None, limit: int = 10, authorization: str | None = Header(None), db = Depends(get_db_dep)):
    require_api_key(authorization)
    """Recent products from the data moat. NOTE: this is a placeholder —
    'trending' currently just means 'most recent', not 'biggest price move'.
    See follow-up tickets for a real trend calculation."""
    q = (
        "SELECT name, store_name, price, currency, line_name, queried_at "
        "FROM price_snapshots WHERE price > 0"
    )
    params: list = []
    if country:
        country_stores = [
            s for s, sv in STORES.items()
            if sv.get("country", "").upper() == country.upper()
        ]
        if not country_stores:
            return {"trending": [], "total": 0, "filter": {"country": country}}
        placeholders = ",".join("?" * len(country_stores))
        q += f" AND store IN ({placeholders})"
        params.extend(country_stores)
    if line:
        q += " AND line = ?"
        params.append(line)
    q += " ORDER BY queried_at DESC LIMIT ?"
    params.append(limit * 2)
    rows = db.execute(q, params).fetchall()
    trending = [dict(r) for r in rows]
    enrich_list(trending)
    return {"trending": trending, "total": len(trending)}


@router.get("/analytics/brands")
def analytics_brands(line: str | None = None, country: str | None = None, limit: int = 20, authorization: str | None = Header(None), db = Depends(get_db_dep)):
    require_api_key(authorization)
    """Top brands in the data moat by snapshot count."""
    q = "SELECT brand, COUNT(*) as count FROM price_snapshots WHERE brand != '' AND price > 0"
    params: list = []
    if line:
        q += " AND line = ?"
        params.append(line)
    country_stores = _country_stores(country)
    if country and not country_stores:
        return {"brands": [], "total": 0}
    if country_stores:
        q += f" AND store IN ({','.join('?' * len(country_stores))})"
        params.extend(country_stores)
    q += " GROUP BY brand ORDER BY count DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(q, params).fetchall()
    return {"brands": [dict(r) for r in rows], "total": len(rows)}


@router.get("/analytics/indicators")
def analytics_indicators(
    country: str | None = None,
    line: str | None = None,
    limit: int = 50,
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    require_api_key(authorization)
    """Latest indicator values from the data moat (internal + public API sources)."""
    values = get_latest_values(db, country=country, line=line, limit=limit)
    return {
        "count": len(values),
        "catalog_size": len(get_indicator_catalog()),
        "country": country,
        "line": line,
        "indicators": values,
    }


def _effective_product_id(row: dict) -> str:
    """canonical_product_id when linked, else the store-local product_id.

    Cross-store price comparison only works on the shared (canonical) id —
    raw product_id is retailer-internal and differs per store. Falling back
    to the raw id when there's no Golden Record link just means that SKU
    won't cross-match, which is the correct, honest behavior (single-store
    SKUs get a null dispersion_score below, not a fabricated one).
    """
    return row.get("canonical_product_id") or row["product_id"]


# A canonical group whose prices span more than this ratio (max/min) is
# more likely a linking/scrape artifact (bundle listing whose contents
# vary, stale promo price mixed with regular price) than real cross-store
# price dispersion — confirmed 2026-07-13 against production gasificadas
# data: 6/44 canonical groups had >3x internal spread, including bundle
# SKUs like "Coca Cola 3L + Inca Kola..." whose price varies with promo
# state, not retailer pricing strategy. Treat those as unreliable rather
# than reporting a fabricated dispersion number.
_MAX_PLAUSIBLE_SPREAD_RATIO = 3.0


def _build_sku_rows(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(_effective_product_id(r), []).append(r)

    out: list[dict] = []
    for r in rows:
        key = _effective_product_id(r)
        peers = groups[key]
        dispersion = None
        if len({p["store"] for p in peers}) >= 2:
            prices = [p["price"] for p in peers if p.get("price")]
            if len(prices) >= 2 and (mean := statistics.mean(prices)) > 0:
                if max(prices) / min(prices) <= _MAX_PLAUSIBLE_SPREAD_RATIO:
                    dispersion = round(statistics.pstdev(prices) / mean, 4)
        discount = r.get("discount") or 0
        out.append({
            "product_id": key,
            "name": r["name"],
            "brand": r["brand"],
            "store": r["store"],
            "store_name": r["store_name"],
            "price": r["price"],
            "list_price": r.get("list_price"),
            "discount": discount or None,
            "currency": r["currency"],
            "promo_active": bool(discount and discount > 0),
            "dispersion_score": dispersion,
        })
    return out


@router.get("/v1/brand-monitor")
def brand_monitor(
    brand: str,
    country: str | None = None,
    line: str | None = None,
    days: int = 30,
    competitors: str | None = None,
    limit: int = 200,
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    require_api_key(authorization)
    """Cross-store SKU snapshot for one brand plus its declared or inferred
    competitors — real price_snapshots data only, no fabricated fields.
    Powers the pricing dashboard's comparator table, promo panel, and alerts.
    """
    country_stores = _country_stores(country)
    if country and not country_stores:
        return {
            "summary": {
                "brand": brand,
                "my_skus_count": 0,
                "my_skus_with_promo": 0,
                "competitor_skus_count": 0,
                "competitor_skus_with_promo": 0,
                "stores_covered": 0,
                "competitors_found": [],
            },
            "my_skus": [],
            "competitor_skus": [],
        }

    since = _since_iso(days)

    competitor_list = [c.strip() for c in competitors.split(",") if c.strip()] if competitors else []
    if not competitor_list:
        cq = (
            "SELECT brand, COUNT(*) as count FROM price_snapshots "
            "WHERE brand != '' AND brand != ? AND price > 0 AND queried_at >= ?"
        )
        cparams: list = [brand, since]
        if line:
            cq += " AND line = ?"
            cparams.append(line)
        if country_stores:
            cq += f" AND store IN ({','.join('?' * len(country_stores))})"
            cparams.extend(country_stores)
        cq += " GROUP BY brand ORDER BY count DESC LIMIT 5"
        competitor_list = [r["brand"] for r in db.execute(cq, cparams).fetchall()]

    all_brands = [brand] + competitor_list
    q = (
        "SELECT product_id, name, brand, store, store_name, price, list_price, discount, "
        "currency, canonical_product_id, queried_at FROM price_snapshots "
        f"WHERE brand IN ({','.join('?' * len(all_brands))}) AND price > 0 AND queried_at >= ?"
    )
    params: list = list(all_brands) + [since]
    if line:
        q += " AND line = ?"
        params.append(line)
    if country_stores:
        q += f" AND store IN ({','.join('?' * len(country_stores))})"
        params.extend(country_stores)
    q += " ORDER BY queried_at DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in db.execute(q, params).fetchall()]

    sku_rows = _build_sku_rows(rows)
    my_skus = [r for r in sku_rows if r["brand"] == brand]
    competitor_skus = [r for r in sku_rows if r["brand"] != brand]

    summary = {
        "brand": brand,
        "my_skus_count": len(my_skus),
        "my_skus_with_promo": sum(1 for r in my_skus if r["promo_active"]),
        "competitor_skus_count": len(competitor_skus),
        "competitor_skus_with_promo": sum(1 for r in competitor_skus if r["promo_active"]),
        "stores_covered": len({r["store"] for r in sku_rows}),
        "competitors_found": sorted({r["brand"] for r in competitor_skus}),
    }

    return {"summary": summary, "my_skus": my_skus, "competitor_skus": competitor_skus}
