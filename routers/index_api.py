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

from index_gate import index_lookup, index_resolve, index_stats
from server_deps import require_api_key

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