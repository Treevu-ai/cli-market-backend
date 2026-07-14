"""Tests for the subcategory param on /v1/intel/scores and /analytics/indicators
(cli-market-core 1.11.43 added subcategory-scoped composite scores — this
covers the backend passthrough, not the core computation itself)."""

from __future__ import annotations

import pytest

from routers import analytics as analytics_router
from routers import intel as intel_router


@pytest.fixture
def pe_store(monkeypatch):
    stores = {"wong_pe": {"country": "PE", "disabled": False, "line": "supermercados", "currency": "PEN"}}
    monkeypatch.setattr(intel_router, "STORES", stores)
    monkeypatch.setattr(intel_router, "require_api_key", lambda *_a, **_k: "test-user")
    monkeypatch.setattr(intel_router, "_cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(intel_router, "_cache_set", lambda *_a, **_k: None)
    monkeypatch.setattr(analytics_router, "STORES", stores)
    monkeypatch.setattr(analytics_router, "require_api_key", lambda *_a, **_k: "test-user")


def test_intel_scores_accepts_subcategory_param(pe_store, isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    with TestClient(app):
        db = isolated_db.get_db()
        db.execute(
            """
            INSERT INTO price_snapshots
                (product_id, name, brand, store, store_name, price, currency, line, discount, queried_at)
            VALUES ('p1', 'Gaseosa Big Cola 400ml', 'bigcola', 'wong_pe', 'Wong', 1.9, 'PEN', 'supermercados', 10, datetime('now'))
            """
        )
        db.commit()
        db.close()

        result = intel_router.intel_scores(
            country="PE", line="supermercados", subcategory="bebidas", authorization="Bearer test"
        )

    assert result["subcategory"] == "bebidas"
    assert "scores" in result


def test_analytics_indicators_accepts_subcategory_param(pe_store, isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    with TestClient(app):
        db = isolated_db.get_db()
        result = analytics_router.analytics_indicators(
            country="PE", line="supermercados", subcategory="bebidas",
            limit=50, authorization="Bearer test", db=db,
        )
        db.close()

    assert result["subcategory"] == "bebidas"
    assert result["line"] == "supermercados"


def test_analytics_indicators_line_level_unaffected_when_subcategory_omitted(pe_store, isolated_db):
    """Regression guard: omitting subcategory must keep today's exact
    line-level behavior (subcategory key present but None)."""
    from fastapi.testclient import TestClient
    from market_server import app

    with TestClient(app):
        db = isolated_db.get_db()
        result = analytics_router.analytics_indicators(
            country="PE", line="supermercados", limit=50, authorization="Bearer test", db=db,
        )
        db.close()

    assert result["subcategory"] is None
