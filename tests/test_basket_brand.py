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
