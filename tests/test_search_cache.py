"""Tests for the in-process search cache added to /products/search.

Covers: cache hit avoids re-fetching stores, TTL expiry triggers a refetch,
partial/error results are never cached, and different query params get
different cache entries.
"""

import pytest

from routers import search


def _product(name, price, store):
    return {
        "id": f"{store}-{name}", "product_id": f"{store}-{name}",
        "name": name, "brand": "Marca", "price": price, "store": store,
        "store_name": store.title(), "currency": "PEN", "category": "abarrotes",
    }


@pytest.fixture(autouse=True)
def _clear_search_cache():
    search._search_cache.clear()
    yield
    search._search_cache.clear()


def _patch_common(monkeypatch, fetch_calls, raw=None, errors=None):
    raw = raw if raw is not None else {"wong": [_product("Arroz 1kg", 4.0, "wong")]}
    errors = errors if errors is not None else []

    async def fake_fetch(*_args, **_kwargs):
        fetch_calls.append(1)
        return raw, errors

    monkeypatch.setattr(search, "require_api_key", lambda _auth: "tester")
    monkeypatch.setattr(search, "_resolve_search_stores", lambda _body: ["wong"])
    monkeypatch.setattr(search, "_parallel_fetch_stores", fake_fetch)
    monkeypatch.setattr(search, "product_from_json", lambda product, _store: product)
    monkeypatch.setattr(search, "enrich_list", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(search, "_attach_source_health", lambda payload, _stores: payload)
    monkeypatch.setattr(search, "save_price_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(search, "save_search_query", lambda *_a, **_k: None)
    monkeypatch.setattr(search, "STORES", {"wong": {"name": "Wong", "currency": "PEN", "line": "supermercados"}})
    monkeypatch.setattr(search, "LINES", {"supermercados": {"name": "Supermercados"}})


@pytest.mark.asyncio
async def test_second_identical_search_hits_cache(monkeypatch):
    fetch_calls = []
    _patch_common(monkeypatch, fetch_calls)

    body = search.SearchRequest(query="arroz")
    r1 = await search.search_products(body, "Bearer token")
    r2 = await search.search_products(body, "Bearer token")

    assert len(fetch_calls) == 1  # second call served from cache, no refetch
    assert r1 == r2


@pytest.mark.asyncio
async def test_different_country_is_a_different_cache_entry(monkeypatch):
    fetch_calls = []
    _patch_common(monkeypatch, fetch_calls)

    await search.search_products(search.SearchRequest(query="arroz", country="PE"), "Bearer token")
    await search.search_products(search.SearchRequest(query="arroz", country="AR"), "Bearer token")

    assert len(fetch_calls) == 2  # different country -> cache miss, real fetch both times


@pytest.mark.asyncio
async def test_expired_entry_triggers_refetch(monkeypatch):
    fetch_calls = []
    _patch_common(monkeypatch, fetch_calls)
    monkeypatch.setattr(search, "_SEARCH_CACHE_TTL", 0.0)  # instantly expired

    body = search.SearchRequest(query="arroz")
    await search.search_products(body, "Bearer token")
    await search.search_products(body, "Bearer token")

    assert len(fetch_calls) == 2


@pytest.mark.asyncio
async def test_partial_result_is_not_cached(monkeypatch):
    fetch_calls = []
    _patch_common(
        monkeypatch,
        fetch_calls,
        raw={"wong": [_product("Arroz 1kg", 4.0, "wong")]},
        errors=[{"store": "metro", "product_id": "?", "error": "timeout"}],
    )

    body = search.SearchRequest(query="arroz")
    r1 = await search.search_products(body, "Bearer token")
    r2 = await search.search_products(body, "Bearer token")

    assert r1.get("partial") is True
    assert len(fetch_calls) == 2  # never cached, so it re-fetched every time
