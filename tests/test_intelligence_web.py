"""Tests for public intelligence web surfaces and MCP tool registration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from intelligence_web import (
    embed_snippet_for_homepage,
    pulse_view_model,
    render_commerce_pulse_page,
)
from market_pulse import build_commerce_pulse
from routers.mcp_http import _TOOLS
from tests.test_market_pulse import SAMPLE_BRIEF, SAMPLE_DASHBOARD


@pytest.fixture
def sample_pulse():
    return build_commerce_pulse(SAMPLE_BRIEF, dashboard=SAMPLE_DASHBOARD, country="PE", lang="es")


def test_pulse_view_model_slim(sample_pulse):
    vm = pulse_view_model(sample_pulse)
    assert vm["country"] == "PE"
    assert vm["kpis"]["inflation_pct"] == 4.8
    assert "brief" not in vm
    assert len(vm["executive_highlights"]) >= 1


def test_render_commerce_pulse_page_contains_sections(sample_pulse):
    html = render_commerce_pulse_page(sample_pulse)
    assert "THIS WEEK IN LATAM COMMERCE" in html
    assert "Executive Highlights" in html
    assert "PVI" in html
    assert "PE" in html


def test_embed_snippet_includes_iframe():
    snippet = embed_snippet_for_homepage("https://api.example.com")
    assert "iframe" in snippet
    assert "/embed/commerce-pulse" in snippet


def test_mcp_tools_include_intelligence_terminal():
    # market_intel_pulse, market_forecast, market_arbitrage were removed from the
    # default profile in the registry (2026-06-29). Assert on the intelligence tools
    # that replaced them in the default profile.
    names = {t["name"] for t in _TOOLS}
    for tool in ("market_intel_brief", "market_inflation", "market_scores", "market_affordability", "market_inflation_report"):
        assert tool in names


def test_intelligence_landing_route(sample_pulse, monkeypatch):
    from fastapi.testclient import TestClient

    from market_server import app

    monkeypatch.setattr("routers.intelligence_web._load_pulse", lambda country, lang="es": sample_pulse)
    monkeypatch.setattr("routers.intelligence_web.check_rate_limit", lambda *a, **k: None)

    client = TestClient(app)
    response = client.get("/intelligence?country=PE")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Executive Highlights" in response.text
    assert "content-security-policy" in {k.lower() for k in response.headers}


def test_embed_commerce_pulse_route(sample_pulse, monkeypatch):
    from fastapi.testclient import TestClient

    from market_server import app

    monkeypatch.setattr("routers.intelligence_web._load_pulse", lambda country, lang="es": sample_pulse)
    monkeypatch.setattr("routers.intelligence_web.check_rate_limit", lambda *a, **k: None)

    client = TestClient(app)
    response = client.get("/embed/commerce-pulse?country=PE")
    assert response.status_code == 200
    assert "embed" in response.text


def test_intelligence_data_json(sample_pulse, monkeypatch):
    from fastapi.testclient import TestClient

    from market_server import app

    monkeypatch.setattr("routers.intelligence_web._load_pulse", lambda country, lang="es": sample_pulse)
    monkeypatch.setattr("routers.intelligence_web.check_rate_limit", lambda *a, **k: None)

    client = TestClient(app)
    response = client.get("/public/intelligence/data?country=PE")
    assert response.status_code == 200
    data = response.json()
    assert data["country"] == "PE"
    assert data["kpis"]["pvi"] == 23.0
