import pytest

from routers import search


def _product(name, price, store):
    return {
        "id": f"{store}-{name}", "product_id": f"{store}-{name}",
        "name": name, "brand": "Marca", "price": price, "store": store,
        "store_name": store.title(), "currency": "PEN", "category": "bebidas",
    }


@pytest.mark.asyncio
async def test_compare_keeps_unit_sixpack_and_twelvepack_separate(monkeypatch):
    raw = {
        "wong": [
            _product("Gaseosa unidad 330ml", 2.0, "wong"),
            _product("Gaseosa sixpack 330ml", 10.0, "wong"),
            _product("Gaseosa twelvepack 330ml", 19.0, "wong"),
        ],
        "metro": [
            _product("Gaseosa unidad 330ml", 1.9, "metro"),
            _product("Gaseosa sixpack 330ml", 9.5, "metro"),
            _product("Gaseosa twelvepack 330ml", 18.5, "metro"),
        ],
    }

    async def fake_fetch(*_args, **_kwargs):
        return raw, []

    monkeypatch.setattr(search, "require_api_key", lambda _auth: "tester")
    monkeypatch.setattr(search, "_resolve_search_stores", lambda _body: ["wong", "metro"])
    monkeypatch.setattr(search, "_parallel_fetch_stores", fake_fetch)
    monkeypatch.setattr(search, "product_from_json", lambda product, _store: product)
    monkeypatch.setattr(search, "enrich_list", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(search, "_attach_source_health", lambda payload, _stores: payload)

    result = await search.compare_products(search.SearchRequest(query="gaseosa"), "Bearer token")
    assert len(result["comparison"]) == 3
    assert len({row["variant_key"] for row in result["comparison"]}) == 3
    assert all(row["match_confidence"]["level"] == "high" for row in result["comparison"])


@pytest.mark.asyncio
async def test_live_basket_partial_retailer_never_wins(monkeypatch):
    products = {
        "leche 1L": {
            "wong": [_product("Leche 1L", 5.0, "wong")],
            "metro": [_product("Leche 1L", 1.0, "metro")],
        },
        "arroz 1kg": {
            "wong": [_product("Arroz 1kg", 4.0, "wong")],
            "metro": [],
        },
    }

    async def fake_fetch_store(store, query, *_args, **_kwargs):
        # basket_compare resolves each item via fetch_store(store, item_name)
        # directly, not _parallel_fetch_stores (that's the search/compare path).
        return products[query].get(store, [])

    monkeypatch.setattr(search, "require_api_key", lambda _auth: "tester")
    monkeypatch.setattr(search, "fetch_store", fake_fetch_store)
    monkeypatch.setattr(search, "product_from_json", lambda product, _store: product)
    monkeypatch.setattr(search, "enrich_list", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(search, "STORES", {
        "wong": {"name": "Wong", "currency": "PEN"},
        "metro": {"name": "Metro", "currency": "PEN"},
    })

    body = search.BasketRequest(
        items=[{"name": "leche 1L"}, {"name": "arroz 1kg"}],
        stores=["wong", "metro"],
    )
    result = await search.basket_compare(body, "Bearer token")
    assert result["best_complete_store"] == "wong"
    assert result["best_store"] == "wong"
    assert result["lowest_partial_total"]["store"] == "metro"
    assert result["comparison"]["metro"]["comparable"] is False
