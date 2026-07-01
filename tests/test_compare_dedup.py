"""Regression test for /products/compare duplicate rows after a successful
fuzzy match — the AR "Leche entera La Serenísima" appearing as two separate
rows across stores despite being the same physical product."""

from __future__ import annotations

import asyncio

import pytest

from routers import search as search_router


@pytest.fixture
def two_stores(monkeypatch):
    stores = {
        "carrefour_ar": {"country": "AR", "disabled": False, "line": "supermercados", "currency": "ARS"},
        "vea_ar": {"country": "AR", "disabled": False, "line": "supermercados", "currency": "ARS"},
    }
    monkeypatch.setattr(search_router, "STORES", stores)
    monkeypatch.setattr(search_router, "require_api_key", lambda *_a, **_k: "test-user")
    monkeypatch.setattr(search_router, "_record_tool_call", lambda *_a, **_k: None)
    monkeypatch.setattr(search_router, "enrich_list", lambda items, store_key="": items)
    monkeypatch.setattr(search_router, "product_from_json", lambda p, store: {**p, "store": store})
    monkeypatch.setattr(search_router, "_resolve_search_stores", lambda body: ["carrefour_ar", "vea_ar"])

    async def _fake_fetch(stores_list, query, page, limit):
        # Same physical product, worded differently per store — the exact
        # AR reproduction: word order + accent differences.
        raw = {
            "carrefour_ar": [
                {"id": "679541", "name": "Leche entera La Serenísima tetra 200cc", "brand": "La Serenisima", "price": 500.0},
            ],
            "vea_ar": [
                {"id": "21110102069", "name": "Leche La Serenisima Entera 200cc", "brand": "La Serenisima", "price": 480.0},
            ],
        }
        return raw, []

    monkeypatch.setattr(search_router, "_parallel_fetch_stores", _fake_fetch)


def test_compare_does_not_duplicate_fuzzy_matched_product(two_stores):
    body = search_router.SearchRequest(query="leche entera", country="AR")
    result = asyncio.run(search_router.compare_products(body, authorization="Bearer test"))

    comparison = result["comparison"]
    assert len(comparison) == 1, f"expected the fuzzy-matched product to appear once, got {len(comparison)} rows: {comparison}"
    assert set(comparison[0]["prices"].keys()) == {"carrefour_ar", "vea_ar"}


def test_compare_includes_per_unit_price(two_stores):
    """Regression: compare's footer claims "normalizado kg/L" but never
    computed a per-unit price — raw totals across different pack sizes
    (200cc vs 1L vs 400g) read as apples-to-oranges."""
    body = search_router.SearchRequest(query="leche entera", country="AR")
    result = asyncio.run(search_router.compare_products(body, authorization="Bearer test"))

    row = result["comparison"][0]
    assert "prices_per_unit" in row
    for store in ("carrefour_ar", "vea_ar"):
        assert store in row["prices_per_unit"]
        assert row["prices_per_unit"][store]["basis"] == "L"
        assert row["prices_per_unit"][store]["price_per"] > 0
