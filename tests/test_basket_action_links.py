"""Regression test for cli-market-world#466 bug #2: --action-links had no
effect. BasketRequest never declared include_action_links, so pydantic
silently dropped the field and basket_compare() never saw it."""

from __future__ import annotations

import asyncio

import pytest

from routers import search as search_router


@pytest.fixture
def pe_store(monkeypatch):
    stores = {
        "wong_pe": {
            "country": "PE", "disabled": False, "line": "supermercados",
            "currency": "PEN", "name": "Wong", "link_base": "https://www.wong.pe",
            "platform": "vtex",
        },
    }
    monkeypatch.setattr(search_router, "STORES", stores)
    monkeypatch.setattr("market_core.store_credentials.get_all_stores", lambda: stores)
    monkeypatch.setattr(search_router, "require_api_key", lambda *_a, **_k: "test-user")
    monkeypatch.setattr(search_router, "_record_tool_call", lambda *_a, **_k: None)
    monkeypatch.setattr(search_router, "enrich_list", lambda items, store_key="": items)

    async def _fake_fetch(store, items):
        return store, {
            "store_name": "Wong",
            "currency": "PEN",
            "items": [{"name": "Leche Descremada 1L", "price": 5.0, "qty": 1, "subtotal": 5.0}],
            "total": 5.0,
            "items_found": 1,
            "items_requested": 1,
        }

    monkeypatch.setattr(search_router, "_fetch_basket_store", _fake_fetch)


def _run(body):
    return asyncio.run(search_router.basket_compare(body, authorization="Bearer test"))


def test_action_links_flag_off_by_default(pe_store):
    body = search_router.BasketRequest(items=[{"name": "leche"}], country="PE")
    result = _run(body)
    assert "action_links" not in result


def test_action_links_flag_attaches_deeplink(pe_store):
    body = search_router.BasketRequest(items=[{"name": "leche"}], country="PE", include_action_links=True)
    result = _run(body)
    assert "action_links" in result
    assert result["action_links"]
    assert "wong.pe" in result["action_links"][0]["url"]
