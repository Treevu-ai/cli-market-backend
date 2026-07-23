"""Regression tests for endpoints that existed as registered MCP tools in
cli-market-core (schema + dispatcher) but were never implemented on this
backend's REST layer — every call returned "Unknown tool" from /mcp.

cli-market-core already ships the compute_*/run_*/list_* business logic
(used by its own optional market_core.api_routes.router); these routes
call it directly.

First 4 (price_risk, informal_signal, promo_detector, retailer_scorecard)
found in an earlier session. market_moat_confidence/market_ecosystem_radar/
market_procurement_bulk found in the 2026-07-23 full MCP tools audit —
unlike the first 4, these three had no route at all anywhere, not just a
missing dispatch entry.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import routers.intel as intel_module


def test_price_risk_route_calls_compute_price_risk(monkeypatch):
    monkeypatch.setattr(intel_module, "require_api_key", lambda auth: "testuser")
    with patch(
        "market_core.market_intel_products.compute_price_risk",
        return_value={"risk_level": "moderate"},
    ) as fake:
        result = intel_module.intel_price_risk(
            country="PE", line="supermercados", days=7, authorization="Bearer x", db="fake-db"
        )
    fake.assert_called_once_with("fake-db", country="PE", line="supermercados", days=7)
    assert result == {"risk_level": "moderate"}


def test_informal_signal_route_calls_compute_informal_signal(monkeypatch):
    monkeypatch.setattr(intel_module, "require_api_key", lambda auth: "testuser")
    with patch(
        "market_core.market_informal_signal.compute_informal_signal",
        return_value={"confidence": "low"},
    ) as fake:
        result = intel_module.intel_informal_signal(
            country="PE", line="supermercados", authorization="Bearer x", db="fake-db"
        )
    fake.assert_called_once_with("fake-db", country="PE", line="supermercados")
    assert result == {"confidence": "low"}


def test_promo_detector_route_calls_compute_promo_authenticity(monkeypatch):
    monkeypatch.setattr(intel_module, "require_api_key", lambda auth: "testuser")
    with patch(
        "market_core.market_promo_detector.compute_promo_authenticity",
        return_value={"authentic": True},
    ) as fake:
        result = intel_module.intel_promo_detector(
            product="aceite", store="wong_pe", days=30, authorization="Bearer x", db="fake-db"
        )
    fake.assert_called_once_with("fake-db", product="aceite", store="wong_pe", days=30)
    assert result == {"authentic": True}


def test_retailer_scorecard_route_calls_compute_retailer_scorecard(monkeypatch):
    monkeypatch.setattr(intel_module, "require_api_key", lambda auth: "testuser")
    with patch(
        "market_core.market_retailer_scorecard.compute_retailer_scorecard",
        return_value={"score": 82},
    ) as fake:
        result = intel_module.intel_retailer_scorecard(
            store="wong_pe", days=30, authorization="Bearer x", db="fake-db"
        )
    fake.assert_called_once_with("fake-db", store="wong_pe", days=30)
    assert result == {"score": 82}


def test_retailer_scorecard_route_maps_value_error_to_422(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(intel_module, "require_api_key", lambda auth: "testuser")
    with patch(
        "market_core.market_retailer_scorecard.compute_retailer_scorecard",
        side_effect=ValueError("unknown store 'nope'"),
    ):
        try:
            intel_module.intel_retailer_scorecard(
                store="nope", days=30, authorization="Bearer x", db="fake-db"
            )
            pytest.fail("expected HTTPException")
        except HTTPException as exc:
            assert exc.status_code == 422
            assert "nope" in exc.detail


# ── 2026-07-23 audit follow-up: 3 more tools with no route at all ───────────
# (market_moat_confidence, market_ecosystem_radar, market_procurement_bulk —
# unlike the 4 above, these had never been implemented anywhere, not even
# as dead code, until this session added both the route and the dispatch.)


def test_moat_confidence_route_calls_compute_moat_confidence(monkeypatch):
    monkeypatch.setattr(intel_module, "require_api_key", lambda auth: "testuser")
    with patch(
        "market_core.market_receipts.compute_moat_confidence",
        return_value={"confidence_tier": "verified"},
    ) as fake:
        result = intel_module.moat_confidence(
            product_id="p1", store="wong_pe", name=None, authorization="Bearer x", db="fake-db"
        )
    fake.assert_called_once_with("fake-db", product_id="p1", store="wong_pe", name=None)
    assert result == {"confidence_tier": "verified"}


def test_ecosystem_launches_route_calls_list_ecosystem_launches(monkeypatch):
    monkeypatch.setattr(intel_module, "require_api_key", lambda auth: "testuser")
    with patch(
        "market_core.market_ecosystem.list_ecosystem_launches",
        return_value={"launches": []},
    ) as fake:
        result = intel_module.ecosystem_launches(
            topic="food", days=7, limit=20, authorization="Bearer x", db="fake-db"
        )
    fake.assert_called_once_with("fake-db", topic="food", days=7, limit=20)
    assert result == {"launches": []}


def test_procurement_bulk_route_calls_run_procurement_bulk(monkeypatch):
    monkeypatch.setattr(intel_module, "require_api_key", lambda auth: "testuser")
    lines = [{"sku_query": "arroz 50kg", "qty": 10, "unit": "kg"}]
    with patch(
        "market_core.market_procurement_bulk.run_procurement_bulk",
        return_value={"status": "ok"},
    ) as fake:
        result = intel_module.intel_procurement_bulk(
            body={"country": "PE", "lines": lines}, authorization="Bearer x", db="fake-db"
        )
    fake.assert_called_once_with(
        "fake-db",
        country="PE",
        lines=lines,
        organization_id=None,
        include_substitutes=True,
        output="json",
    )
    assert result == {"status": "ok"}


def test_procurement_bulk_route_rejects_empty_lines(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(intel_module, "require_api_key", lambda auth: "testuser")
    try:
        intel_module.intel_procurement_bulk(
            body={"country": "PE", "lines": []}, authorization="Bearer x", db="fake-db"
        )
        pytest.fail("expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 422
