"""Product search, comparison, and metadata endpoints.

Endpoints:
  POST /products/search                  Multi-store search (parallel batch)
  POST /products/compare                 Cross-store comparison + fuzzy match
  POST /v1/basket/compare                Multi-item cart comparison across stores
  GET  /products/stock/{product_id}      Latest stock from price_snapshots
  GET  /products/delivery/{product_id}   Placeholder delivery info
  GET  /products/barcode/{code}          OpenFoodFacts barcode lookup
  GET  /products/enrich                  OpenFoodFacts search
  GET  /categories/{store}               VTEX category tree
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import os
import re

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from market_core import (
    get_default_stores,
    LINES,
    PAGE_SIZE,
    STORES,
    fetch_store,
    get_db,
    product_from_json,
    save_price_snapshot,
    save_search_query,
)
from store_credentials import get_store_profile, store_exists

logger = logging.getLogger("market.server").getChild("search")

router = APIRouter(tags=["search"])


def _resolve_search_stores(body: SearchRequest) -> list[str]:
    stores = [body.store] if body.store else get_default_stores()
    stores = [s for s in stores if store_exists(s)]
    if body.line and body.line in LINES:
        stores = [s for s in stores if (get_store_profile(s) or {}).get("line") == body.line]
    return stores


class SearchRequest(BaseModel):
    query: str
    store: str | None = None
    line: str | None = None
    page: int = 1
    limit: int = PAGE_SIZE

    @field_validator("query")
    @classmethod
    def sanitize_query(cls, v: str) -> str:
        v = v.strip()[:200]
        if not v:
            raise ValueError("Query no puede estar vacío")
        return re.sub(r"[<>{}()\[\]]", "", v)


class BasketRequest(BaseModel):
    items: list[dict]
    stores: list[str] | None = None


@router.post("/products/search")
async def search_products(body: SearchRequest):
    """Multi-store parallel search. Stores are queried in batches of PARALLEL_BATCH;
    a per-batch timeout prevents a slow store from holding up the whole response."""
    try:
        return await _search_products(body)
    except Exception as e:
        logger.exception("search_products crashed")
        raise HTTPException(status_code=500, detail=str(e))


async def _search_products(body: SearchRequest):
    stores = _resolve_search_stores(body)

    PARALLEL_BATCH = 20
    SEARCH_TIMEOUT = float(os.getenv("SEARCH_TIMEOUT", "15.0"))

    async def fetch_one(store: str):
        try:
            raw = await fetch_store(store, body.query, body.page, body.limit)
            return store, raw, None
        except Exception as e:
            return store, [], str(e)

    results: list[dict] = []
    errors: list[dict] = []
    for i in range(0, len(stores), PARALLEL_BATCH):
        batch = stores[i : i + PARALLEL_BATCH]
        batch_tasks = [fetch_one(s) for s in batch]
        try:
            batch_results = await asyncio.wait_for(asyncio.gather(*batch_tasks), timeout=SEARCH_TIMEOUT)
        except asyncio.TimeoutError:
            for s in batch:
                errors.append({"store": s, "error": "timeout"})
            break
        except Exception as e:
            logger.error("Search batch error: %s", e)
            errors.append({"store": "batch", "error": str(e)})
            break
        for store, raw, err in batch_results:
            if err:
                errors.append({"store": store, "error": err})
                continue
            for p in raw:
                try:
                    prod = product_from_json(p, store)
                    prod["line"] = STORES[store]["line"]
                    prod["line_name"] = LINES[STORES[store]["line"]]["name"]
                    results.append(prod)
                except Exception as pe:
                    errors.append({"store": store, "product_id": str(p)[:80], "error": str(pe)})

    results.sort(key=lambda p: p["price"] if p["price"] > 0 else float("inf"))
    for p in results:
        save_price_snapshot(p)
    save_search_query(body.query, body.line, body.store, len(results))

    response: dict = {"query": body.query, "results": results, "total": len(results)}
    if errors:
        response["partial"] = True
        response["errors"] = errors
    return response


@router.post("/products/compare")
async def compare_products(body: SearchRequest):
    """Cross-store comparison with brand+name fuzzy matching."""
    stores = _resolve_search_stores(body)
    all_raw: dict[str, list] = {}
    for store in stores:
        try:
            all_raw[store] = await fetch_store(store, body.query, body.page, body.limit)
        except Exception:
            all_raw[store] = []

    all_products = {}
    for s, raw in all_raw.items():
        all_products[s] = []
        for p in raw:
            try:
                all_products[s].append(product_from_json(p, s))
            except Exception:
                pass

    def match_key(p: dict) -> str:
        name = re.sub(r"[^a-záéíóúñ0-9]", "", p["name"].lower())
        return f"{p['brand'].lower()}|{name}"

    key_index: dict[str, dict] = {}
    for store, prods in all_products.items():
        for p in prods:
            k = match_key(p)
            key_index.setdefault(k, {})[store] = p

    FUZZY_THRESHOLD = 0.70
    store_list = list(stores)
    for i in range(len(store_list)):
        for j in range(i + 1, len(store_list)):
            sa, sb = store_list[i], store_list[j]
            only_a = [k for k, sp in key_index.items() if sa in sp and sb not in sp]
            only_b = [k for k, sp in key_index.items() if sb in sp and sa not in sp]
            matched_b: set[str] = set()
            for ka in only_a:
                prod_a = key_index[ka][sa]
                best_score = 0.0
                best_kb = None
                for kb in only_b:
                    if kb in matched_b:
                        continue
                    score = difflib.SequenceMatcher(
                        None, match_key(prod_a), match_key(key_index[kb][sb])
                    ).ratio()
                    if score > best_score:
                        best_score = score
                        best_kb = kb
                if best_score >= FUZZY_THRESHOLD and best_kb:
                    key_index[ka][sb] = key_index[best_kb][sb]
                    matched_b.add(best_kb)

    comparison: list[dict] = []
    for _k, sp in key_index.items():
        if len(sp) >= 1:
            prices = {s: p["price"] for s, p in sp.items() if p["price"] > 0}
            if prices:
                best = min(prices, key=prices.get)
                rep = sp[list(sp.keys())[0]]
                comparison.append(
                    {
                        "name": rep["name"],
                        "brand": rep["brand"],
                        "prices": prices,
                        "best_store": best,
                        "best_price": prices[best],
                    }
                )

    comparison.sort(key=lambda x: x["best_price"])
    return {"query": body.query, "comparison": comparison, "stores_compared": len(stores)}


@router.post("/v1/basket/compare")
async def basket_compare(body: BasketRequest):
    """Take a list of items + optional stores list, return the cheapest store
    for the combined basket. Each item is searched in each store; missing
    items are skipped."""
    stores = body.stores or list(STORES.keys())
    stores = [s for s in stores if s in STORES]
    results: dict[str, dict] = {}
    for store in stores:
        t = 0.0
        found: list[dict] = []
        for item in body.items:
            try:
                raw = await fetch_store(store, item["name"])
                if raw:
                    best = min(
                        raw,
                        key=lambda p: float(
                            (
                                p.get("items", [{}])[0]
                                .get("sellers", [{}])[0]
                                .get("commertialOffer", {})
                                .get("Price", 0)
                                or 0
                            )
                            or float("inf")
                        ),
                    )
                    prod = product_from_json(best, store)
                    q = item.get("qty", 1)
                    t += prod["price"] * q
                    found.append(
                        {
                            "name": prod["name"][:40],
                            "price": prod["price"],
                            "qty": q,
                            "subtotal": round(prod["price"] * q, 2),
                        }
                    )
            except Exception:
                continue
        if found:
            results[store] = {
                "store_name": STORES[store]["name"],
                "currency": STORES[store]["currency"],
                "items": found,
                "total": round(t, 2),
                "items_found": len(found),
                "items_requested": len(body.items),
            }
    best = min(results, key=lambda s: results[s]["total"]) if results else None
    return {
        "source": "live",
        "basket": body.items,
        "comparison": results,
        "best_store": best,
        "best_total": results[best]["total"] if best else None,
        "stores_compared": len(results),
    }


@router.get("/products/stock/{product_id}")
def product_stock(product_id: str, store: str):
    """Latest stock snapshot for a product in a specific store."""
    db = get_db()
    row = db.execute(
        "SELECT stock, name, store_name FROM price_snapshots "
        "WHERE product_id=? AND store=? ORDER BY queried_at DESC LIMIT 1",
        (product_id, store),
    ).fetchone()
    db.close()
    if not row:
        return {"product_id": product_id, "store": store, "stock": None, "message": "No data"}
    return {
        "product_id": product_id,
        "store": store,
        "stock": row["stock"],
        "name": row["name"],
        "store_name": row["store_name"],
    }


@router.get("/products/delivery/{product_id}")
def product_delivery(product_id: str, store: str, zipcode: str = ""):
    """Placeholder delivery info — currently just returns the store URL."""
    store_info = STORES.get(store, {})
    return {
        "product_id": product_id,
        "store": store,
        "store_name": store_info.get("name", store),
        "delivery_available": True,
        "estimated_days": "2-5",
        "message": "Delivery integration pending. Check the store directly.",
        "store_url": f"{store_info.get('base','')}/{product_id}/p",
    }


@router.get("/products/barcode/{code}")
def barcode_lookup(code: str):
    """OpenFoodFacts barcode → product metadata."""
    r = httpx.get(f"https://world.openfoodfacts.org/api/v2/product/{code}.json", timeout=10)
    if r.status_code == 200:
        product = r.json().get("product", {})
        return {
            "code": code,
            "name": product.get("product_name", ""),
            "brand": product.get("brands", ""),
            "nutriscore": product.get("nutriscore_grade", "").upper(),
            "categories": product.get("categories", ""),
        }
    return {"code": code, "error": "not found"}


@router.get("/products/enrich")
def enrich_products(query: str, limit: int = 5):
    """OpenFoodFacts text search."""
    r = httpx.get(
        f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={query}&json=1&page_size={limit}",
        timeout=10,
    )
    if r.status_code == 200:
        products = r.json().get("products", [])
        results = []
        for p in products:
            results.append(
                {
                    "name": p.get("product_name", ""),
                    "brand": p.get("brands", ""),
                    "nutriscore": p.get("nutriscore_grade", "").upper(),
                    "barcode": p.get("code", ""),
                }
            )
        return {"results": results, "total": r.json().get("count", 0)}
    return {"results": [], "total": 0}


@router.get("/categories/{store}")
async def categories(store: str):
    """VTEX category tree (depth 10) for a store."""
    base = STORES.get(store, {}).get("base", "")
    if not base:
        raise HTTPException(status_code=404, detail="Tienda no encontrada")
    url = f"{base}/api/catalog_system/pub/category/tree/10"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        return resp.json()