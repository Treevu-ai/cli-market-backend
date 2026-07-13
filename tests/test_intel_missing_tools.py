"""Regression tests for 4 endpoints that existed as registered MCP tools in
cli-market-core (schema + dispatcher) but were never implemented on this
backend's REST layer — every call returned "Unknown tool" from /mcp.

cli-market-core already ships the compute_* business logic (used by its own
optional market_core.api_routes.router); these routes call it directly.
"""

from __future__ import annotations

from unittest.mock import patch

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
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 422
            assert "nope" in exc.detail
