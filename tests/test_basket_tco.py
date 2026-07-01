"""Regression test for cli-market-backend#130: include_tco/include_delivery
were accepted by BasketRequest's payload shape but never declared on the
pydantic model, so they were silently dropped and --tco had no effect."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest

from routers import search as search_router


@pytest.fixture
def pe_store(monkeypatch):
    stores = {"wong_pe": {"country": "PE", "disabled": False, "line": "supermercados", "currency": "PEN"}}
    monkeypatch.setattr(search_router, "STORES", stores)
    monkeypatch.setattr(search_router, "require_api_key", lambda *_a, **_k: "test-user")
    monkeypatch.setattr(search_router, "_record_tool_call", lambda *_a, **_k: None)


def _run(body):
    return asyncio.run(search_router.basket_compare(body, authorization="Bearer test"))


def test_include_tco_routes_to_build_basket_compare(monkeypatch, pe_store):
    captured = {}

    def _fake_build_basket_compare(db, **kwargs):
        captured.update(kwargs)
        return {"items_searched": 1, "items_found": 1, "stores": [{"store": "wong_pe", "total": 10.0, "tco_total": 12.0}]}

    monkeypatch.setattr(search_router, "build_basket_compare", _fake_build_basket_compare)
    monkeypatch.setattr(search_router, "get_db", lambda: type("DB", (), {"close": lambda self: None})())

    body = search_router.BasketRequest(items=[{"name": "leche"}], country="PE", include_tco=True)
    result = _run(body)

    assert captured["include_tco"] is True
    assert result["source"] == "snapshot"
    assert result["stores"][0]["tco_total"] == 12.0


def test_default_path_unchanged_without_include_tco(monkeypatch, pe_store):
    called = {"build_basket_compare": False}

    def _fake_build_basket_compare(db, **kwargs):
        called["build_basket_compare"] = True
        return {}

    async def _fake_fetch(store, items):
        return store, {
            "store_name": "Wong", "currency": "PEN",
            "items": [{"name": "Leche 1L", "price": 5.0, "qty": 1, "subtotal": 5.0}],
            "total": 5.0, "items_found": 1, "items_requested": 1,
        }

    monkeypatch.setattr(search_router, "build_basket_compare", _fake_build_basket_compare)
    monkeypatch.setattr(search_router, "enrich_list", lambda items, store_key="": items)
    monkeypatch.setattr(search_router, "_fetch_basket_store", _fake_fetch)

    body = search_router.BasketRequest(items=[{"name": "leche"}], country="PE")
    result = _run(body)

    assert called["build_basket_compare"] is False
    assert result["source"] == "live"
