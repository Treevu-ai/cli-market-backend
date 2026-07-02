"""Regression test for GET /v1/intel/brief delegating to build_intel_brief().

intel_brief() used to reimplement the brief inline (compute_composite_scores +
compute_basket_stress + get_latest_values, assembled by hand) instead of
calling market_core.market_indicators.build_intel_brief(), which already
existed with the same purpose. The inline version never returned macro_gap,
sources, or disclaimer, silently dropped the per-score "confidence" flag, and
read moat_freshness/store_coverage from a cache that build_intel_brief already
fixed to read live (cli-market-backend#127 S6/S7) — this endpoint now uses
that fix instead of its own stale duplicate.
"""

from __future__ import annotations

import routers.intel as intel_module


def test_intel_brief_delegates_to_build_intel_brief(monkeypatch):
    calls = []

    def fake_build_intel_brief(db, *, country=None, line=None, days=7, include_catalog=False):
        calls.append({"db": db, "country": country, "line": line, "days": days, "include_catalog": include_catalog})
        return {
            "headline": "PE: +1.2% en 7d",
            "shelf": {},
            "macro_gap": {"shelf_vs_cpi_gap_pp": 3.4},
            "confidence": {"moat_freshness_pct": 92.0, "stores_active": 5},
            "scores": {"retail_aggression": {"score": 40.0, "label": "moderate", "confidence": "high"}},
            "sources": ["price_history", "worldbank"],
            "disclaimer": "Online modern retail channel...",
            "enrichment": {"indicators": [], "total": 0},
            "subcategories": {"subcategories": [], "total": 0},
            "analytics": {"indicators": [], "total": 0},
        }

    monkeypatch.setattr(intel_module, "build_intel_brief", fake_build_intel_brief)
    monkeypatch.setattr(intel_module, "require_api_key", lambda auth: "testuser")
    intel_module._cache.clear()

    result = intel_module.intel_brief(country="PE", line=None, days=7, include_catalog=False, authorization="Bearer x", db="fake-db")

    assert len(calls) == 1
    assert calls[0] == {"db": "fake-db", "country": "PE", "line": None, "days": 7, "include_catalog": False}
    # The fields the old inline implementation silently dropped:
    assert result["macro_gap"] == {"shelf_vs_cpi_gap_pp": 3.4}
    assert result["sources"] == ["price_history", "worldbank"]
    assert result["disclaimer"]
    assert result["scores"]["retail_aggression"]["confidence"] == "high"


def test_intel_brief_caches_result_without_recomputing(monkeypatch):
    calls = []

    def fake_build_intel_brief(db, **kwargs):
        calls.append(kwargs)
        return {"headline": "cached test"}

    monkeypatch.setattr(intel_module, "build_intel_brief", fake_build_intel_brief)
    monkeypatch.setattr(intel_module, "require_api_key", lambda auth: "testuser")
    intel_module._cache.clear()

    first = intel_module.intel_brief(country="CO", line=None, days=7, include_catalog=False, authorization=None, db="fake-db")
    second = intel_module.intel_brief(country="CO", line=None, days=7, include_catalog=False, authorization=None, db="fake-db")

    assert first == second == {"headline": "cached test"}
    assert len(calls) == 1
