"""Semantic index REST API — Golden Record resolve / lookup / stats.

Endpoints:
  POST /index/resolve     Resolve a retailer snapshot to a Golden Record
  GET  /index/lookup/{id} Fetch canonical product by prod_* id
  GET  /index/stats       Registry + linkage metrics
  GET  /resolve           Query-string alias for resolve (GET convenience)
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from index_gate import backfill_canonical_product_ids, index_lookup, index_resolve, index_stats
from server_deps import require_admin, require_api_key

router = APIRouter(tags=["index"])


class ResolveRequest(BaseModel):
    name: str
    brand: str = ""
    store: str = ""
    sku: str = ""
    price: float = 0.0
    currency: str = "USD"
    url: str = ""


@router.post("/index/resolve")
def resolve_product(
    body: ResolveRequest,
    authorization: str | None = Header(None),
):
    require_api_key(authorization)
    return index_resolve(body.model_dump())


@router.get("/resolve")
def resolve_product_get(
    name: str = Query(..., min_length=1),
    brand: str = "",
    store: str = "",
    sku: str = "",
    price: float = 0.0,
    currency: str = "USD",
    authorization: str | None = Header(None),
):
    """GET alias — same as POST /index/resolve for simple agent calls."""
    require_api_key(authorization)
    return index_resolve(
        {
            "name": name,
            "brand": brand,
            "store": store,
            "sku": sku,
            "price": price,
            "currency": currency,
        }
    )


@router.get("/index/lookup/{product_id}")
def lookup_product(product_id: str, authorization: str | None = Header(None)):
    require_api_key(authorization)
    result = index_lookup(product_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    return result


@router.get("/index/stats")
def stats(authorization: str | None = Header(None)):
    require_api_key(authorization)
    return index_stats()


@router.post("/index/backfill")
def backfill(
    authorization: str | None = Header(None),
    limit: int = Query(1000, ge=1, le=5000),
    batches: int = Query(1, ge=1, le=50),
    dry_run: bool = False,
):
    """Admin: resolve unlinked snapshots and stamp canonical_product_id."""
    require_admin(authorization)
    totals = {
        "resolved": 0,
        "linked": 0,
        "skipped": 0,
        "errors": 0,
        "exact": 0,
        "fuzzy": 0,
        "auto": 0,
    }
    before = index_stats()
    batch_results: list[dict] = []

    for batch_num in range(1, batches + 1):
        stats = backfill_canonical_product_ids(limit=limit, dry_run=dry_run)
        stats["batch"] = batch_num
        batch_results.append(stats)
        for key in totals:
            totals[key] += int(stats.get(key, 0))
        if stats.get("fetched", 0) == 0 or stats.get("resolved", 0) == 0:
            break

    after = index_stats()
    return {
        "before": before,
        "after": after,
        "totals": totals,
        "batches_run": len(batch_results),
        "batch_results": batch_results,
        "dry_run": dry_run,
    }