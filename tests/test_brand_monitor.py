"""Tests for GET /v1/brand-monitor — cross-store SKU snapshot for a brand
plus its competitors. Was a confirmed 404 (route didn't exist) that also
crashed the pricing dashboard's render when the frontend tried to spread
undefined my_skus/competitor_skus off the 404 body.
"""

from __future__ import annotations

import pytest

from routers import analytics as analytics_router


@pytest.fixture
def pe_store(monkeypatch):
    stores = {
        "wong_pe": {"country": "PE", "disabled": False, "line": "supermercados", "currency": "PEN"},
        "plaza_vea_pe": {"country": "PE", "disabled": False, "line": "supermercados", "currency": "PEN"},
    }
    monkeypatch.setattr(analytics_router, "STORES", stores)
    monkeypatch.setattr(analytics_router, "require_api_key", lambda *_a, **_k: "test-user")


def _seed(db):
    rows = [
        # Same canonical product across two stores -> dispersion_score should be non-null.
        ("p1_wong", "Arroz Paisana 1kg", "paisana", "wong_pe", "Wong", 5.0, 5.0, 0, "prod_paisana_arroz_1kg"),
        ("p1_pv", "Arroz Paisana 1kg", "paisana", "plaza_vea_pe", "Plaza Vea", 6.0, 6.0, 0, "prod_paisana_arroz_1kg"),
        # Single-store product -> dispersion_score should be null.
        ("p2_wong", "Arroz Paisana 5kg", "paisana", "wong_pe", "Wong", 20.0, 20.0, 0, "prod_paisana_arroz_5kg"),
        # Competitor brand, on promo (discount > 0).
        ("c1_wong", "Arroz Costeño 1kg", "costeno", "wong_pe", "Wong", 4.5, 5.5, 18, None),
        # A third brand — should not appear when explicit competitors= is passed.
        ("c2_wong", "Arroz Faraon 1kg", "faraon", "wong_pe", "Wong", 4.0, 4.0, 0, None),
    ]
    for pid, name, brand, store, store_name, price, list_price, discount, canonical in rows:
        db.execute(
            """
            INSERT INTO price_snapshots
                (product_id, name, brand, store, store_name, price, list_price, discount,
                 currency, line, canonical_product_id, queried_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PEN', 'supermercados', ?, datetime('now'))
            """,
            (pid, name, brand, store, store_name, price, list_price, discount, canonical),
        )
    db.commit()


def test_brand_monitor_returns_my_and_competitor_skus_with_dispersion(pe_store, isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    with TestClient(app):
        db = isolated_db.get_db()
        _seed(db)

        result = analytics_router.brand_monitor(
            brand="paisana",
            country="PE",
            line="supermercados",
            days=30,
            competitors="costeno",
            limit=200,
            authorization="Bearer test",
            db=db,
        )

    assert result["summary"]["brand"] == "paisana"
    assert result["summary"]["my_skus_count"] == 3
    assert result["summary"]["competitor_skus_count"] == 1
    assert result["summary"]["competitor_skus_with_promo"] == 1
    assert result["summary"]["competitors_found"] == ["costeno"]
    # faraon was excluded — an explicit competitors= list must not auto-expand.
    assert "faraon" not in {r["brand"] for r in result["my_skus"] + result["competitor_skus"]}

    by_id = {r["product_id"]: r for r in result["my_skus"]}
    assert by_id["prod_paisana_arroz_1kg"]["dispersion_score"] is not None
    assert by_id["prod_paisana_arroz_5kg"]["dispersion_score"] is None

    competitor_row = result["competitor_skus"][0]
    assert competitor_row["promo_active"] is True
    assert competitor_row["discount"] == 18


def test_brand_monitor_auto_selects_competitors_when_none_given(pe_store, isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    with TestClient(app):
        db = isolated_db.get_db()
        _seed(db)

        result = analytics_router.brand_monitor(
            brand="paisana",
            country="PE",
            line="supermercados",
            days=30,
            competitors=None,
            limit=200,
            authorization="Bearer test",
            db=db,
        )

    # Both other brands in scope should be picked up automatically.
    assert set(result["summary"]["competitors_found"]) == {"costeno", "faraon"}


def test_brand_monitor_unknown_country_returns_empty_not_error(pe_store, isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    with TestClient(app):
        db = isolated_db.get_db()
        _seed(db)

        result = analytics_router.brand_monitor(
            brand="paisana",
            country="ZZ",
            line=None,
            days=30,
            competitors=None,
            limit=200,
            authorization="Bearer test",
            db=db,
        )

    assert result["my_skus"] == []
    assert result["summary"]["stores_covered"] == 0
