"""Regression test for cli-market-backend#135 (O4/O5): GET /categories/{store}
is a raw pass-through of the retailer's own live VTEX category tree, with no
connection to price_snapshots — a category can appear empty/absent here with
no relation to real search/compare availability. Adds a disclaimer instead
of the bare tree."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import asyncio

import httpx
import pytest

from routers import search as search_router


def test_categories_wraps_tree_with_disclaimer(monkeypatch):
    monkeypatch.setattr(search_router, "require_api_key", lambda *_a, **_k: "test-user")
    monkeypatch.setattr(
        search_router,
        "STORES",
        {"carrefour": {"base": "https://www.carrefour.com.ar"}},
    )

    class _Resp:
        def json(self):
            return [{"name": "Almacén", "children": []}]

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _Resp()

    monkeypatch.setattr(search_router.httpx, "AsyncClient", _FakeClient)

    result = asyncio.run(search_router.categories("carrefour", authorization="Bearer test"))
    assert result["store"] == "carrefour"
    assert result["categories"] == [{"name": "Almacén", "children": []}]
    assert "disclaimer" in result
    assert "no está sincronizado" in result["disclaimer"]
