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
from fastapi import APIRouter, Depends, Header, HTTPException
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
from server_deps import get_db_dep, require_api_key
from index_gate import enrich_list
from market_core.market_action_links import retailer_deeplink
from market_core.market_basket import build_basket_compare
from market_core.market_food_match import infer_staple_from_query, matches_food_basket_query
from market_core.market_units import price_per_base_unit
from http_retry import request_with_retry

logger = logging.getLogger("market.server").getChild("search")

router = APIRouter(tags=["search"])


# ── Relevance filter ────────────────────────────────────────────────────────────────────────────────────

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


# ── REST API funnel instrumentation ─────────────────────────────────────────────────────────────────────────────

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
    line: str | None = None
    country: str | None = None
    include_action_links: bool = False
    include_tco: bool = False
    include_delivery: bool = True


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

    # _is_relevant alone is a plain OR word-boundary match — for a canasta
    # staple query like "arroz" it accepts vinegar, crackers, flour, or
    # infant cereal that happen to have the word "arroz" in the name, none
    # of which are the requested staple (cli-market-backend#127 N1: compare
    # "arroz" listed vinagre de arroz, harina de arroz, galletas de arroz,
    # Nestum Arroz alongside real rice). Only gate on the stricter
    # exclusion-aware staple matcher when the query is confidently a known
    # canasta staple — matches_food_basket_query's generic (non-staple)
    # fallback path requires ALL tokens (AND), which would wrongly tighten
    # matching for ordinary multi-word compare searches unrelated to any
    # staple.
    is_staple_query = infer_staple_from_query(body.query) is not None

    all_products = {}
    for s, raw in all_raw.items():
        all_products[s] = []
        for p in raw:
            try:
                prod = product_from_json(p, s)
                name = prod.get("name", "")
                if q_tokens and not _is_relevant(name, q_tokens):
                    continue
                if is_staple_query and not matches_food_basket_query(body.query, {"name": name, "line": prod.get("line", "")}):
                    continue
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
                    # A successful merge folds best_kb's entry into ka — drop
                    # the original key_index[best_kb] or the same physical
                    # product shows up twice in the final comparison: once
                    # under ka (now with both stores) and once orphaned under
                    # its original key (cli-market-world#... AR duplicate
                    # rows finding, e.g. "Leche entera La Serenísima" listed
                    # separately per store despite a successful fuzzy match).
                    del key_index[best_kb]

    comparison: list[dict] = []
    for _k, sp in key_index.items():
        if len(sp) >= 1:
            prices = {s: p["price"] for s, p in sp.items() if p["price"] > 0}
            if prices:
                best = min(prices, key=prices.get)
                rep = sp[list(sp.keys())[0]]
                # Per-store price-per-unit (kg/L) so pack-size mismatches
                # between stores (e.g. 200cc vs 1L vs 400g powder) are
                # visible instead of comparing raw prices across different
                # units — the "normalizado kg/L" footer claimed this but
                # compare never actually computed it (cli-market-world#...
                # search/compare/enrich AR/CO findings).
                prices_per_unit = {}
                for s, p in sp.items():
                    if p["price"] <= 0:
                        continue
                    ppu = price_per_base_unit(p["price"], p["name"])
                    if ppu:
                        prices_per_unit[s] = ppu
                comparison.append(
                    {
                        "name": rep["name"],
                        "brand": rep["brand"],
                        "prices": prices,
                        "prices_per_unit": prices_per_unit,
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


async def _fetch_basket_store(
    store: str,
    items: list[dict],
) -> tuple[str, dict | None]:
    """Fetch all basket items for a single store in parallel, return (store, result_or_None)."""

    async def resolve_item(item: dict) -> dict | None:
        try:
            raw = await fetch_store(store, item["name"])
            if not raw:
                return None
            q_tokens = _query_tokens(item["name"])
            candidates: list[dict] = []
            for p in raw:
                try:
                    prod = product_from_json(p, store)
                    name = prod.get("name", "")
                    if q_tokens and not _is_relevant(name, q_tokens, require_all=True):
                        continue
                    # Word-boundary token matching alone lets candy/condiment
                    # products slip through when the staple word appears in
                    # their name (e.g. "Chocolate con Leche", "Sazonador sabor
                    # Arroz") — infer_category's staple-equality check never
                    # caught this because its taxonomy dependency was missing
                    # and it silently no-opped. matches_food_basket_query
                    # applies the same staple-exclusion list already used
                    # elsewhere in the moat (cli-market-backend#127 basket
                    # matching investigation).
                    if not matches_food_basket_query(item["name"], {"name": name, "line": "supermercados"}):
                        continue
                    candidates.append(prod)
                except Exception:
                    continue
            if not candidates:
                return None
            best_prod = min(
                candidates,
                key=lambda p: p["price"] if p["price"] > 0 else float("inf"),
            )
            q = item.get("qty", 1)
            return {
                "name": best_prod["name"][:40],
                "price": best_prod["price"],
                "qty": q,
                "subtotal": round(best_prod["price"] * q, 2),
            }
        except Exception:
            logger.debug(
                "basket item resolution failed for store=%s item=%s",
                store, item.get("name"), exc_info=True,
            )
            return None

    item_results = await asyncio.gather(*[resolve_item(item) for item in items])
    found = [r for r in item_results if r is not None]
    if not found:
        return store, None
    total = round(sum(r["subtotal"] for r in found), 2)
    return store, {
        "store_name": STORES[store]["name"],
        "currency": STORES[store]["currency"],
        "items": found,
        "total": total,
        "items_found": len(found),
        "items_requested": len(items),
    }


@router.post("/v1/basket/compare")
async def basket_compare(body: BasketRequest, authorization: str | None = Header(None)):
    """Take a list of items + optional stores list, return the cheapest store
    for the combined basket. Stores and items are fetched concurrently in
    batches; a total timeout prevents slow stores from blocking the response."""
    username = require_api_key(authorization)
    _record_tool_call(authorization, "basket_compare", username)
    stores = body.stores or list(STORES.keys())
    stores = [s for s in stores if s in STORES]
    if body.line and body.line in LINES:
        stores = [s for s in stores if STORES.get(s, {}).get("line") == body.line]
    if body.country:
        cc = body.country.strip().upper()
        stores = [s for s in stores if STORES.get(s, {}).get("country") == cc]

    if body.include_tco:
        # include_tco/include_delivery were accepted by this request model
        # but never wired to anything — pydantic silently dropped them and
        # --tco had no effect (cli-market-backend#130). market_core's
        # build_basket_compare already has real TCO math (market_tco.py) and
        # delivery-quote simulation; the live per-store resolver above never
        # grew that logic. It reads price_snapshots (indexed catalog)
        # instead of live store APIs for item pricing, so it can be a few
        # hours behind live — surfaced explicitly via data_freshness/
        # data_age_hours on each store row rather than labeled "live" like
        # the default path (AGENTS.md data-gate: don't present stale moat
        # data as fresh). TCO/delivery numbers themselves are computed live
        # (simulate_delivery_quote hits the delivery API first, falls back
        # to static defaults only if that fails).
        db = get_db()
        try:
            result = build_basket_compare(
                db,
                items=body.items,
                store_filter=set(stores) if stores else None,
                include_tco=True,
                include_delivery=body.include_delivery,
                include_action_links=body.include_action_links,
                country=body.country,
            )
        finally:
            db.close()
        result["source"] = "snapshot"
        return result

    parallel_batch = int(os.getenv("BASKET_PARALLEL_BATCH", "8"))
    timeout_s = float(os.getenv("BASKET_TIMEOUT", "25.0"))
    results: dict[str, dict] = {}

    for i in range(0, len(stores), parallel_batch):
        batch = stores[i : i + parallel_batch]
        batch_tasks = [_fetch_basket_store(s, body.items) for s in batch]
        try:
            batch_results = await asyncio.wait_for(
                asyncio.gather(*batch_tasks), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            logger.warning(
                "basket_compare batch timeout at stores[%d:%d]", i, i + parallel_batch
            )
            break
        except Exception as e:
            logger.error("basket_compare batch error: %s", e)
            break
        for store, store_data in batch_results:
            if store_data is not None:
                results[store] = store_data

    # ── Index Enrichment ──
    for store_data in results.values():
        enrich_list(store_data["items"], store_key=store_data.get("store_name", ""))
    # ───────────────────
    best = min(results, key=lambda s: results[s]["total"]) if results else None

    payload: dict = {
        "source": "live",
        "basket": body.items,
        "comparison": results,
        "best_store": best,
        "best_total": results[best]["total"] if best else None,
        "stores_compared": len(results),
    }
    if body.include_action_links and best and body.items:
        # BasketRequest never declared this field before, so pydantic silently
        # dropped it and the CLI's --action-links flag had no effect at all
        # (cli-market-world#466). Minimal fix: a search-mode deeplink into the
        # winning store for the first requested item — not the full
        # product-level/handoff action-link set market_core.market_action_links
        # supports, which needs per-item product_id/db context this endpoint
        # doesn't track today.
        first_item_name = str(body.items[0].get("name") or "")
        link = retailer_deeplink(best, name=first_item_name) if first_item_name else None
        payload["action_links"] = [link] if link else []
    return payload


@router.get("/products/stock/{product_id}")
def product_stock(product_id: str, store: str, authorization: str | None = Header(None), db = Depends(get_db_dep)):
    """Latest stock snapshot for a product in a specific store."""
    require_api_key(authorization)
    row = db.execute(
        "SELECT stock, name, store_name FROM price_snapshots "
        "WHERE product_id=? AND store=? ORDER BY queried_at DESC LIMIT 1",
        (product_id, store),
    ).fetchone()
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
    """Referential delivery estimate — VTEX defaults/simulation when available."""
    store_info = STORES.get(store, {})
    message = "Estimación referencial. Confirmar plazo, costo y cobertura con el retailer."
    fee = None
    source = None
    delivery_available = False
    estimated_days = "—"

    try:
        from market_core.market_tco import simulate_delivery_quote

        quote = simulate_delivery_quote(
            store,
            subtotal=0.0,
            product_id=product_id,
            zipcode=zipcode or None,
        )
        if quote.get("available"):
            delivery_available = True
            fee = quote.get("fee")
            source = quote.get("source") or "referential"
            estimated_days = "2-5"
    except Exception:
        pass

    return {
        "product_id": product_id,
        "store": store,
        "store_name": store_info.get("name", store),
        "delivery_available": delivery_available,
        "estimated_days": estimated_days,
        "fee": fee,
        "source": source,
        "referential": True,
        "message": message,
        "store_url": f"{store_info.get('base','')}/{product_id}/p",
    }


@router.get("/products/barcode/{code}")
def barcode_lookup(code: str):
    """OpenFoodFacts barcode → product metadata."""
    if not code.strip().isdigit() or not (8 <= len(code.strip()) <= 14):
        # EAN-8/12/13/14 are the only formats OFF indexes by barcode — catch
        # this before hitting the network so the CLI can point the user at
        # search instead of the generic "not found" hint (O6,
        # cli-market-backend#127).
        return {"code": code, "error": "invalid barcode format", "status": 400}
    try:
        r = request_with_retry(
            "GET", f"https://world.openfoodfacts.org/api/v2/product/{code}.json", timeout=10
        )
    except httpx.RequestError as e:
        logger.warning("barcode lookup network failure for %s: %s", code, e)
        return {"code": code, "error": f"network error contacting Open Food Facts ({type(e).__name__})", "status": 503}
    if r.status_code == 200:
        product = r.json().get("product", {})
        return {
            "code": code,
            "name": product.get("product_name", ""),
            "brand": product.get("brands", ""),
            "nutriscore": product.get("nutriscore_grade", "").upper(),
            "categories": product.get("categories", ""),
        }
    return {"code": code, "error": "not found", "status": 404}


@router.get("/products/enrich")
def enrich_products(query: str, limit: int = 5, authorization: str | None = Header(None)):
    """OpenFoodFacts text search."""
    require_api_key(authorization)
    try:
        r = request_with_retry(
            "GET",
            "https://world.openfoodfacts.org/cgi/search.pl",
            params={
                "search_terms": query,
                # Raw string concatenation used to build the URL here
                # (f"...search_terms={query}...") instead of a proper params
                # dict — query characters like & or # could break the query
                # string structure. search_simple/action match the params
                # market_core.market_enrich_sources.fetch_off_by_search
                # already uses (cli-market-backend#132, T1/O-enrich finding).
                "search_simple": 1,
                "action": "process",
                "json": 1,
                "page_size": limit,
            },
            timeout=10,
        )
    except httpx.RequestError as e:
        # Previously any network failure fell through to `return {"results":
        # [], "total": 0}` below with a 200 status — the CLI read that as a
        # genuine "0 results" and exited 0, hiding that OFF was actually
        # unreachable ("El exit code es 0 — el sistema cree que funcionó").
        logger.warning("OFF enrich search failed for %r: %s", query, e)
        raise HTTPException(status_code=503, detail=f"Open Food Facts unreachable ({type(e).__name__})")
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
    """VTEX category tree (depth 10) for a store.

    This is a raw pass-through of the retailer's own live catalog
    structure — it is NOT cross-referenced against price_snapshots, so a
    category can appear here (or appear empty/with 0 subcategories) with no
    relation to what CLI Market has actually indexed and can search/compare
    (cli-market-backend#127/#135: Olímpica's tree had no grocery category at
    all despite search returning grocery results; Carrefour AR's tree had
    entire branches — carnes, frutas, congelados — with 0 populated
    subcategories). Use `market search`/`market discover` to check real
    product availability, not this endpoint.
    """
    require_api_key(authorization)
    base = STORES.get(store, {}).get("base", "")
    if not base:
        raise HTTPException(status_code=404, detail="Tienda no encontrada")
    url = f"{base}/api/catalog_system/pub/category/tree/10"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        tree = resp.json()
    return {
        "store": store,
        "categories": tree,
        "disclaimer": (
            "Árbol de categorías en vivo de la tienda (VTEX) — no está sincronizado con el catálogo "
            "indexado por CLI Market. Una categoría puede aparecer vacía o ausente aquí sin relación "
            "con la disponibilidad real de productos. Usá market search / market discover para "
            "verificar disponibilidad real."
        ),
    }
