"""Read-only analytics over price_snapshots.

Endpoints:
  GET /analytics/price-history   Snapshots filtered by product/store/line
  GET /analytics/stats           Totals + last snapshot timestamp
  GET /analytics/trending        Recent products (placeholder — sorted by queried_at)
  GET /analytics/brands          Top brands by snapshot count
  GET /analytics/indicators      Latest moat indicator values
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from market_core import STORES
from market_indicators import get_indicator_catalog, get_latest_values
from server_deps import get_db_dep, require_api_key
from index_gate import enrich_list

router = APIRouter(tags=["analytics"])


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
