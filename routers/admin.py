"""Operational / admin endpoints — used by the dashboard and ops scripts.

Endpoints:
  GET  /admin/debug-fetch     Test fetch_store + product_from_json for one store/query
  POST /admin/collect         Trigger a price collection run synchronously
  POST /v1/admin/scan-stores  Probe known retailer domains for liveness

Protected with MARKET_API_TOKEN (Bearer). Set on Railway before exposing publicly.
"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Body, Header

from market_core import STORES, fetch_store, product_from_json
from server_deps import require_admin

router = APIRouter(prefix="", tags=["admin"])


@router.get("/admin/debug-fetch")
async def debug_fetch(
    store: str = "wong",
    query: str = "leche",
    authorization: str | None = Header(None),
):
    require_admin(authorization)
    """Smoke test the data pipeline for one store: raw fetch → normalize."""
    raw = await fetch_store(store, query, page=1, limit=3)
    products = [product_from_json(p, store) for p in raw[:3]]
    return {"store": store, "query": query, "results": len(raw), "products": products}


@router.post("/admin/collect")
async def admin_collect(
    stores: int = 0,
    queries: int = 0,
    authorization: str | None = Header(None),
):
    require_admin(authorization)
    """Trigger a price collection run directly (synchronous).

    Useful for manual smoke testing on Render after deploys. Use ?stores=2&queries=2
    for a quick sanity check; default runs the full catalog.
    """
    from collect_prices import (
        build_query_list,
        _get_feedback_db,
        run_collection,
    )
    from market_core import ensure_db_initialized

    ensure_db_initialized()

    sl = list(STORES.keys())
    if stores:
        sl = sl[:stores]

    db = _get_feedback_db()
    ql = build_query_list(db=db, cycle=0)
    if queries:
        ql = ql[:queries]

    t0 = time.monotonic()
    result = await run_collection(sl, ql)
    return {
        "status": "ok",
        "elapsed_s": round(time.monotonic() - t0, 1),
        "stores_attempted": result["stores_attempted"],
        "stores_succeeded": result["stores_succeeded"],
        "prices_collected": result["prices_collected"],
    }


@router.post("/v1/admin/scan-stores")
async def admin_scan_stores(
    body: dict = Body(default_factory=dict),
    authorization: str | None = Header(None),
):
    require_admin(authorization)
    """Probe each known retailer with a tiny VTEX catalog query."""
    line_filter = body.get("line")
    candidates: list[dict] = []
    for sk, sv in STORES.items():
        if line_filter and sv.get("line") != line_filter:
            continue
        base = sv["base"]
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                r = await client.get(
                    f"{base}/api/catalog_system/pub/products/search/test?_from=0&_to=1"
                )
                candidates.append(
                    {
                        "store": sk,
                        "name": sv["name"],
                        "status": r.status_code,
                        "ok": r.status_code in (200, 206),
                    }
                )
        except Exception as e:
            candidates.append(
                {
                    "store": sk,
                    "name": sv["name"],
                    "status": 0,
                    "ok": False,
                    "error": str(e)[:100],
                }
            )
    ok = [c for c in candidates if c["ok"]]
    return {"scanned": len(candidates), "working": len(ok), "candidates": candidates}
