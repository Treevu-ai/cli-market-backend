"""/products/search must surface each result's price confidence.

save_price_snapshot already computes compute_snapshot_confidence(price,
list_price) at write time (discount scrape-error detection), but that value
never made it back into the live search response — only into the internal
ops dashboard. A shopper comparing prices across 41 retailers had no signal
for "is this discount/price trustworthy or a scrape glitch" even though the
backend already flags it.
"""

from __future__ import annotations

import pytest

from routers import search


def _product(name, price, store, list_price=None):
    p = {
        "id": f"{store}-{name}", "product_id": f"{store}-{name}",
        "name": name, "brand": "Marca", "price": price, "store": store,
        "store_name": store.title(), "currency": "PEN", "category": "abarrotes",
    }
    if list_price is not None:
        p["list_price"] = list_price
    return p


@pytest.fixture(autouse=True)
def _clear_search_cache():
    search._search_cache.clear()
    yield
    search._search_cache.clear()


def _patch_common(monkeypatch, raw):
    async def fake_fetch(*_args, **_kwargs):
        return raw, []

    monkeypatch.setattr(search, "require_api_key", lambda _auth: "tester")
    monkeypatch.setattr(search, "_resolve_search_stores", lambda _body: ["wong"])
    monkeypatch.setattr(search, "_parallel_fetch_stores", fake_fetch)
    monkeypatch.setattr(search, "product_from_json", lambda product, _store: dict(product))
    monkeypatch.setattr(search, "enrich_list", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(search, "_attach_source_health", lambda payload, _stores: payload)
    monkeypatch.setattr(search, "save_price_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(search, "save_search_query", lambda *_a, **_k: None)
    monkeypatch.setattr(search, "STORES", {"wong": {"name": "Wong", "currency": "PEN", "line": "supermercados"}})
    monkeypatch.setattr(search, "LINES", {"supermercados": {"name": "Supermercados"}})


@pytest.mark.asyncio
async def test_normal_discount_is_marked_ok(monkeypatch):
    _patch_common(monkeypatch, {"wong": [_product("Arroz 1kg", 4.0, "wong", list_price=4.5)]})

    r = await search.search_products(search.SearchRequest(query="arroz"), "Bearer token")

    assert r["results"][0]["confidence"] == "ok"


@pytest.mark.asyncio
async def test_scrape_error_discount_is_marked_suspect(monkeypatch):
    # >=90% implied discount is almost always a bad list_price scrape
    # (e.g. wrong field mapped), not a real promotion.
    _patch_common(monkeypatch, {"wong": [_product("Arroz 1kg", 1.0, "wong", list_price=100.0)]})

    r = await search.search_products(search.SearchRequest(query="arroz"), "Bearer token")

    assert r["results"][0]["confidence"] == "suspect"
