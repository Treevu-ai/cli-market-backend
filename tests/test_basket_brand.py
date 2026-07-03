"""Regression test for cli-market#B: the live basket path (_fetch_basket_store,
no --tco) dropped brand/product_id even though product_from_json already
returns them — the CLI table had no way to distinguish models/brands."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest

from routers import search as search_router


@pytest.fixture
def pe_stores(monkeypatch):
    stores = {
        "wong_pe": {"country": "PE", "name": "Wong", "currency": "PEN", "line": "supermercados"},
        "metro": {"country": "PE", "name": "Metro", "currency": "PEN", "line": "supermercados"},
    }
    monkeypatch.setattr(search_router, "STORES", stores)


def test_fetch_basket_store_includes_brand_and_product_id(monkeypatch, pe_stores):
    async def _fake_fetch_store(store, term, page=1, limit=20):
        return [{"raw": "product"}]

    def _fake_product_from_json(p, store):
        return {
            "id": "sku-123",
            "name": "Leche Entera Gloria 1L",
            "brand": "Gloria",
            "price": 4.5,
            "store": store,
        }

    monkeypatch.setattr(search_router, "fetch_store", _fake_fetch_store)
    monkeypatch.setattr(search_router, "product_from_json", _fake_product_from_json)
    monkeypatch.setattr(search_router, "_is_relevant", lambda *a, **k: True)
    monkeypatch.setattr(search_router, "matches_food_basket_query", lambda *a, **k: True)

    store, result = asyncio.run(
        search_router._fetch_basket_store("wong_pe", [{"name": "leche", "qty": 2}])
    )

    assert store == "wong_pe"
    item = result["items"][0]
    assert item["brand"] == "Gloria"
    assert item["product_id"] == "sku-123"
    assert item["subtotal"] == 9.0


def test_fetch_basket_store_brand_none_when_missing(monkeypatch, pe_stores):
    async def _fake_fetch_store(store, term, page=1, limit=20):
        return [{"raw": "product"}]

    def _fake_product_from_json(p, store):
        return {"id": "", "name": "Leche Evaporada", "brand": "", "price": 3.9, "store": store}

    monkeypatch.setattr(search_router, "fetch_store", _fake_fetch_store)
    monkeypatch.setattr(search_router, "product_from_json", _fake_product_from_json)
    monkeypatch.setattr(search_router, "_is_relevant", lambda *a, **k: True)
    monkeypatch.setattr(search_router, "matches_food_basket_query", lambda *a, **k: True)

    _, result = asyncio.run(
        search_router._fetch_basket_store("metro", [{"name": "leche evaporada"}])
    )

    assert result["items"][0]["brand"] is None


def test_fetch_basket_store_includes_alternates(monkeypatch, pe_stores):
    """Fix C: _fetch_basket_store used to commit silently to whichever
    candidate was cheapest — a buyer had no way to see other matched
    brands/models at the same store."""
    catalog = {
        "gloria": {"id": "sku-1", "name": "Leche Entera Gloria 1L", "brand": "Gloria", "price": 4.5},
        "laive": {"id": "sku-2", "name": "Leche Entera Laive 1L", "brand": "Laive", "price": 3.9},
        "ideal": {"id": "sku-3", "name": "Leche Evaporada Ideal 400g", "brand": "Ideal", "price": 5.2},
    }

    async def _fake_fetch_store(store, term, page=1, limit=20):
        return [{"key": k} for k in catalog]

    def _fake_product_from_json(p, store):
        return {**catalog[p["key"]], "store": store}

    monkeypatch.setattr(search_router, "fetch_store", _fake_fetch_store)
    monkeypatch.setattr(search_router, "product_from_json", _fake_product_from_json)
    monkeypatch.setattr(search_router, "_is_relevant", lambda *a, **k: True)
    monkeypatch.setattr(search_router, "matches_food_basket_query", lambda *a, **k: True)

    _, result = asyncio.run(
        search_router._fetch_basket_store("wong_pe", [{"name": "leche entera", "qty": 1}])
    )

    item = result["items"][0]
    assert item["brand"] == "Laive"  # cheapest wins, same as before this fix
    alternates = item["alternates"]
    assert [a["brand"] for a in alternates] == ["Gloria", "Ideal"]
    assert alternates[0]["price"] == 4.5


def test_fetch_basket_store_alternates_dedup_same_sku(monkeypatch, pe_stores):
    """Regression: a store API returning the same SKU twice for one query
    used to be able to surface a duplicate of the winner as an "alternate",
    defeating the point of showing genuinely different brands."""

    async def _fake_fetch_store(store, term, page=1, limit=20):
        return [{"n": 0}, {"n": 1}, {"n": 2}]

    def _fake_product_from_json(p, store):
        if p["n"] in (0, 1):
            # Same SKU returned twice by the upstream store API.
            return {"id": "sku-1", "name": "Leche Gloria 1L", "brand": "Gloria", "price": 4.5, "store": store}
        return {"id": "sku-2", "name": "Leche Laive 1L", "brand": "Laive", "price": 3.9, "store": store}

    monkeypatch.setattr(search_router, "fetch_store", _fake_fetch_store)
    monkeypatch.setattr(search_router, "product_from_json", _fake_product_from_json)
    monkeypatch.setattr(search_router, "_is_relevant", lambda *a, **k: True)
    monkeypatch.setattr(search_router, "matches_food_basket_query", lambda *a, **k: True)

    _, result = asyncio.run(
        search_router._fetch_basket_store("wong_pe", [{"name": "leche"}])
    )

    item = result["items"][0]
    assert item["brand"] == "Laive"  # sku-2 is cheaper, wins
    alternates = item["alternates"]
    assert len(alternates) == 1  # only sku-1 remains after dedup, not two copies of it
    assert alternates[0]["product_id"] == "sku-1"


def test_fetch_basket_store_dedup_keeps_cheapest_duplicate(monkeypatch, pe_stores):
    """Regression (CodeRabbit review on #157): the initial dedup fix used a
    dict comprehension that kept the LAST duplicate seen, not the cheapest.
    A store API returning the same SKU twice with different prices (promo,
    stale cache) must not let the more expensive one win."""

    async def _fake_fetch_store(store, term, page=1, limit=20):
        return [{"n": 0}, {"n": 1}]

    def _fake_product_from_json(p, store):
        # Same SKU (sku-1) returned twice with DIFFERENT prices — the second
        # occurrence (n=1) is pricier and would win under last-write-wins.
        price = 4.5 if p["n"] == 0 else 6.0
        return {"id": "sku-1", "name": "Leche Gloria 1L", "brand": "Gloria", "price": price, "store": store}

    monkeypatch.setattr(search_router, "fetch_store", _fake_fetch_store)
    monkeypatch.setattr(search_router, "product_from_json", _fake_product_from_json)
    monkeypatch.setattr(search_router, "_is_relevant", lambda *a, **k: True)
    monkeypatch.setattr(search_router, "matches_food_basket_query", lambda *a, **k: True)

    _, result = asyncio.run(
        search_router._fetch_basket_store("wong_pe", [{"name": "leche"}])
    )

    item = result["items"][0]
    assert item["price"] == 4.5  # cheapest duplicate wins, not the last one seen
    assert item["alternates"] == []  # both occurrences collapse into one candidate


def test_fetch_basket_store_alternates_empty_with_single_candidate(monkeypatch, pe_stores):
    async def _fake_fetch_store(store, term, page=1, limit=20):
        return [{"raw": "product"}]

    def _fake_product_from_json(p, store):
        return {"id": "sku-1", "name": "Leche Gloria 1L", "brand": "Gloria", "price": 4.5, "store": store}

    monkeypatch.setattr(search_router, "fetch_store", _fake_fetch_store)
    monkeypatch.setattr(search_router, "product_from_json", _fake_product_from_json)
    monkeypatch.setattr(search_router, "_is_relevant", lambda *a, **k: True)
    monkeypatch.setattr(search_router, "matches_food_basket_query", lambda *a, **k: True)

    _, result = asyncio.run(
        search_router._fetch_basket_store("wong_pe", [{"name": "leche"}])
    )

    assert result["items"][0]["alternates"] == []
