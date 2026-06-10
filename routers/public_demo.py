"""Public demo flows — mirror of cli-market-world (P1-B)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from routers.search import SearchRequest, _search_products
from server_deps import check_rate_limit

logger = logging.getLogger("market.server").getChild("public_demo")

router = APIRouter(prefix="/public", tags=["public"])

DEMO_QUERIES = frozenset({"arroz", "leche"})
CACHE_TTL = int(os.getenv("PUBLIC_DEMO_CACHE_TTL", "3600"))

_cache: dict[str, dict] = {}
_refresh_lock = asyncio.Lock()


class DemoSessionRequest(BaseModel):
    fingerprint: str = ""


@router.post("/demo/session")
def create_demo_session(
    request: Request,
    body: DemoSessionRequest | None = None,
    x_demo_fingerprint: str | None = Header(None, alias="X-Demo-Fingerprint"),
):
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(f"demo-session:{client_ip}")
    from market_core.demo_tokens import issue_demo_token

    fp = (x_demo_fingerprint or (body.fingerprint if body else "") or "").strip()
    return {"ok": True, **issue_demo_token(client_ip=client_ip, fingerprint=fp)}


async def _compare_via_search(body: SearchRequest) -> dict:
    """Compare using search path (backend has inline compare)."""
    search = await _search_products(body)
    results = search.get("results", [])
    by_name: dict[str, dict] = {}
    for p in results:
        key = (p.get("brand", ""), p.get("name", ""))
        store = p.get("store", "")
        price = float(p.get("price") or 0)
        if price <= 0:
            continue
        bucket = by_name.setdefault(
            key,
            {"name": p.get("name", ""), "brand": p.get("brand", ""), "prices": {}},
        )
        bucket["prices"][store] = price
    comparison = []
    for item in by_name.values():
        prices = item["prices"]
        if not prices:
            continue
        best = min(prices, key=prices.get)
        comparison.append(
            {
                **item,
                "best_store": best,
                "best_price": prices[best],
            }
        )
    comparison.sort(key=lambda x: x["best_price"])
    payload = {
        "query": body.query,
        "comparison": comparison,
        "stores_compared": len({s for c in comparison for s in c["prices"]}),
    }
    if search.get("source_health"):
        payload["source_health"] = search["source_health"]
    return payload


@router.get("/demo/compare")
async def public_demo_compare(request: Request, q: str = "arroz"):
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(f"public-demo:{client_ip}")

    query = q.strip().lower()[:50]
    if query not in DEMO_QUERIES:
        raise HTTPException(
            status_code=400,
            detail=f"Demo query must be one of: {sorted(DEMO_QUERIES)}",
        )

    now = time.time()
    entry = _cache.get(query)
    if entry and now - entry["ts"] < CACHE_TTL:
        out = dict(entry["data"])
        out.update({"demo": True, "cached_at": datetime.fromtimestamp(entry["ts"], tz=timezone.utc).isoformat()})
        return out

    async with _refresh_lock:
        entry = _cache.get(query)
        if entry and now - entry["ts"] < CACHE_TTL:
            out = dict(entry["data"])
            out.update({"demo": True, "cached_at": datetime.fromtimestamp(entry["ts"], tz=timezone.utc).isoformat()})
            return out
        try:
            data = await _compare_via_search(SearchRequest(query=query, limit=5))
            if data.get("comparison"):
                _cache[query] = {"data": data, "ts": time.time()}
                data["demo"] = True
                return data
        except Exception:
            logger.exception("public_demo refresh failed for %s", query)

    raise HTTPException(status_code=503, detail="Demo compare temporarily unavailable")
