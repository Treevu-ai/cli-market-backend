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
import unicodedata

import httpx
from fastapi import APIRouter, Header, HTTPException
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
from server_deps import require_api_key
from index_gate import enrich_list
from http_retry import request_with_retry

logger = logging.getLogger("market.server").getChild("search")

router = APIRouter(tags=["search"])


# ── Relevance filter ──────────────────────────────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Lowercase, strip accents (panó → pano), keep alphanum+spaces."""
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9\s]", " ", text)


def _word_set(text: str) -> frozenset[str]:
    return frozenset(w for w in _normalize_text(text).split() if len(w) >= 2)


def _query_tokens(query: str) -> list[str]:
    """Normalized tokens from the user query (min 2 chars)."""
    return [w for w in _normalize_text(query).split() if len(w) >= 2]


def _is_relevant(product_name: str, q_tokens: list[str], *, require_all: bool = False) -> bool:
    """True if the query tokens appear as complete words in the product name.

    Matching is word-boundary based to prevent prefix false-positives: query
    'pan' should not match 'pantalon' because 'pan' is not a standalone word there.

    require_all=False (default): at least one token must match. Used by search and
    compare, where the caller sees the full result list and picks themselves.
    require_all=True: every token must match. Used by the basket auto-picker, which
    selects a single product per item with no human in the loop; one-token matching
    there silently picks cross-brand / cross-type products (e.g. query
    'leche evaporada gloria entera' matching 'Shake Capuccino UHT Gloria').
    """
    if not q_tokens:
        return True
    name_words = _word_set(product_name)
    if require_all:
        return all(qt in name_words for qt in q_tokens)
    return any(qt in name_words for qt in q_tokens)


# ── REST API funnel instrumentation ───────────────────────────────────────────────────

def _record_tool_call(
    authorization: str | None,
    tool: str,
    username: str,
    *,
    country: str | None = None,
) -> None:
    """Fire mcp_tool_call funnel event for direct REST API usage (non-MCP-HTTP path).
    Surfaces agent activity in /dashboard/mcp under client='api'."""
    if not authorization or username.startswith("demo:"):
        return
    try:
        from market_funnel import is_test_funnel_traffic, record_funnel_event
        if is_test_funnel_traffic(username):
            return
        raw_token = authorization.removeprefix("Bearer ").strip()
        record_funnel_event(
            "mcp_tool_call",
            session_id=raw_token[:20],
            meta={"client": "api", "tool": tool, "country": country or None},
        )
    except Exception:
        pass


# ────────────────────────────────────────────────────────────────────

def _attach_source_health(response: dict, store_ids: list[str]) -> dict:
    try:
        from market_core.source_health import health_for_stores

        db = get_db()
        try:
            response["source_health"] = health_for_stores(db, store_ids)
        finally:
            db.close()
    except Exception as exc:
        logger.debug("source_health attach skipped: %s", exc)
    return response


def _resolve_search_stores(body: SearchRequest) -> list[str]:
    stores = [body.store] if body.store else get_default_stores()
    stores = [s for s in stores if store_exists(s)]
    if body.line and body.line in LINES:
        stores = [s for s in stores if (get_store_profile(s) or {}).get("line") == body.line]
    if body.country:
        cc = body.country.strip().upper()
        stores = [s for s in stores if STORES.get(s, {}).get("country") == cc]
    return stores


class SearchRequest(BaseModel):
    query: str
    store: str | None = None
    line: str | None = None
    country: str | None = None
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
async def search_products(body: SearchRequest, authorization: str | None = Header(None)):
    """Multi-store parallel search. Stores are queried in batches of PARALLEL_BATCH;
    a per-batch timeout prevents a slow store from holding up the whole response."""
    username = require_api_key(authorization)
    _record_tool_call(authorization, "search_products", username, country=body.country)
    try:
        result = await _search_products(body)
        if username.startswith("demo:"):
            try:
                from market_funnel import record_funnel_event

                record_funnel_event(
                    "demo_first_tool_call",
                    session_id=username.split(":", 1)[-1],
                    meta={"tool": "search", "query": body.query, "agent_source": "demo"},
                    dedupe=True,
                )
            except Exception:
                logger.debug("record_funnel_event(demo_first_tool_call) failed", exc_info=True)
        try:
            from market_funnel import maybe_first_search
            maybe_first_search(username, query=body.query)
        except Exception:
            logger.debug("maybe_first_search failed", exc_info=True)
        return result
    except Exception as e:
        logger.exception("search_products crashed")
        raise HTTPException(status_code=500, detail=str(e))


async def _parallel_fetch_stores(
    stores: list[str],
    query: str,
    page: int,
    limit: int,
) -> tuple[dict[str, list], list[dict]]:
    """Fetch retailer product lists in parallel batches (shared by search + compare)."""
    parallel_batch = 20
    timeout_s = float(os.getenv("SEARCH_TIMEOUT", "15.0"))
    all_raw: dict[str, list] = {}
    errors: list[dict] = []

    async def fetch_one(store: str):
        try:
            raw = await fetch_store(store, query, page, limit)
            return store, raw, None
        except Exception as e:
            return store, [], str(e)

    for i in range(0, len(stores), parallel_batch):
        batch = stores[i : i + parallel_batch]
        batch_tasks = [fetch_one(s) for s in batch]
        try:
            batch_results = await asyncio.wait_for(asyncio.gather(*batch_tasks), timeout=timeout_s)
        except asyncio.TimeoutError:
            errors.extend({"store": s, "error": "timeout"} for s in batch)
            break
        except Exception as e:
            logger.error("Fetch batch error: %s", e)
            errors.append({"store": "batch", "error": str(e)})
            break
        for store, raw, err in batch_results:
            if err:
                errors.append({"store": store, "error": err})
            else:
                all_raw[store] = raw
    return all_raw, errors


async def _search_products(body: SearchRequest):
    stores = _resolve_search_stores(body)
    all_raw, errors = await _parallel_fetch_stores(stores, body.query, body.page, body.limit)

    results: list[dict] = []
    for store, raw in all_raw.items():
        for p in raw:
            try:
                prod = product_from_json(p, store)
                prod["line"] = STORES[store]["line"]
                _line = STORES[store]["line"]
                prod["line_name"] = LINES.get(_line, {}).get("name", _line)
                results.append(prod)
            except Exception as pe:
                errors.append({"store": store, "product_id": str(p)[:80], "error": str(pe)})

    # Post-filter: discard results where no query word is a complete word in the
    # product name.  Prevents prefix false-positives from retailer APIs, e.g.
    # query "pan" matching "pantalon jogger" because VTEX returns prefix matches.
    q_tokens = _query_tokens(body.query)
    if q_tokens:
        before = len(results)
        results = [p for p in results if _is_relevant(p.get("name", ""), q_tokens)]
        filtered = before - len(results)
        if filtered:
            logger.debug(
                "relevance_filter removed %d/%d results for query=%r",
                filtered, before, body.query,
            )

    results.sort(key=lambda p: p["price"] if p["price"] > 0 else float("inf"))
    for p in results:
        save_price_snapshot(p)
    save_search_query(body.query, body.line, body.store, len(results))

    # ── Index Enrichment ──
    enrich_list(results)
    # ───────────────────

    response: dict = {"query": body.query, "results": results, "total": len(results)}
    if errors:
        response["partial"] = True
        response["errors"] = errors
    return _attach_source_health(response, stores)


@router.post("/products/compare")
async def compare_products(body: SearchRequest, authorization: str | None = Header(None)):
    """Cross-store comparison with brand+name fuzzy matching."""
    username = require_api_key(authorization)
    _record_tool_call(authorization, "compare_products", username, country=body.country)
    if username.startswith("demo:"):
        try:
            from market_funnel import record_funnel_event

            record_funnel_event(
                "demo_first_tool_call",
                session_id=username.split(":", 1)[-1],
                meta={"tool": "compare", "query": body.query, "agent_source": "demo"},
                dedupe=True,
            )
        except Exception:
            logger.debug("record_funnel_event(demo_first_tool_call) failed", exc_info=True)
    stores = _resolve_search_stores(body)
    all_raw, errors = await _parallel_fetch_stores(stores, body.query, body.page, body.limit)

    q_tokens = _query_tokens(body.query)

    all_products = {}
    for s, raw in all_raw.items():
        all_products[s] = []
        for p in raw:
            try:
                prod = product_from_json(p, s)
                if not q_tokens or _is_relevant(prod.get("name", ""), q_tokens):
                    all_products[s].append(prod)
            except Exception:
                logger.debug("product_from_json failed for store=%s", s, exc_info=True)

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
    # ── Index Enrichment ──
    enrich_list(comparison)
    # ───────────────────
    payload: dict = {"query": body.query, "comparison": comparison, "stores_compared": len(all_raw)}
    if body.country:
        payload["country"] = body.country.strip().upper()
    if errors:
        payload["partial"] = True
        payload["errors"] = errors
    return _attach_source_health(payload, list(all_raw.keys()) or stores)


@router.post("/v1/basket/compare")
async def basket_compare(body: BasketRequest, authorization: str | None = Header(None)):
    """Take a list of items + optional stores list, return the cheapest store
    for the combined basket. Each item is searched in each store; missing
    items are skipped."""
    username = require_api_key(authorization)
    _record_tool_call(authorization, "basket_compare", username)
    stores = body.stores or list(STORES.keys())
    stores = [s for s in stores if s in STORES]
    results: dict[str, dict] = {}
    for store in stores:
        t = 0.0
        found: list[dict] = []
        for item in body.items:
            try:
                raw = await fetch_store(store, item["name"])
                if not raw:
                    continue
                # Convert and filter: only keep products where query words appear
                # as complete words in the product name.  Prevents false matches
                # like "huevos" → "pegatinas de gel de Pascua" or "sal" → "ensalada".
                q_tokens = _query_tokens(item["name"])
                candidates: list[dict] = []
                for p in raw:
                    try:
                        prod = product_from_json(p, store)
                        if not q_tokens or _is_relevant(
                            prod.get("name", ""), q_tokens, require_all=True
                        ):
                            candidates.append(prod)
                    except Exception:
                        continue
                if not candidates:
                    continue
                best_prod = min(
                    candidates,
                    key=lambda p: p["price"] if p["price"] > 0 else float("inf"),
                )
                q = item.get("qty", 1)
                t += best_prod["price"] * q
                found.append(
                    {
                        "name": best_prod["name"][:40],
                        "price": best_prod["price"],
                        "qty": q,
                        "subtotal": round(best_prod["price"] * q, 2),
                    }
                )
            except Exception:
                logger.debug("basket item resolution failed for store=%s", store, exc_info=True)
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
    # ── Index Enrichment ──
    for store_data in results.values():
        enrich_list(store_data["items"], store_key=store_data.get("store_name", ""))
    # ───────────────────
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
def product_stock(product_id: str, store: str, authorization: str | None = Header(None)):
    """Latest stock snapshot for a product in a specific store."""
    require_api_key(authorization)
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
    r = request_with_retry(
        "GET", f"https://world.openfoodfacts.org/api/v2/product/{code}.json", timeout=10
    )
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
def enrich_products(query: str, limit: int = 5, authorization: str | None = Header(None)):
    """OpenFoodFacts text search."""
    require_api_key(authorization)
    r = request_with_retry(
        "GET",
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
async def categories(store: str, authorization: str | None = Header(None)):
    """VTEX category tree (depth 10) for a store."""
    require_api_key(authorization)
    base = STORES.get(store, {}).get("base", "")
    if not base:
        raise HTTPException(status_code=404, detail="Tienda no encontrada")
    url = f"{base}/api/catalog_system/pub/category/tree/10"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        return resp.json()
