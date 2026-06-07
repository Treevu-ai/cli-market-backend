"""Dashboard semantic moat UI blocks."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_dashboard_data_has_semantic_kpis(isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    with TestClient(app) as client:
        r = client.get("/dashboard/data")
    assert r.status_code == 200
    body = r.json()
    assert "linkage_pct" in body["kpis"]
    assert "snapshots_linked" in body["kpis"]
    view = body.get("dashboard_view") or {}
    semantic = (view.get("blocks") or {}).get("semantic_moat")
    assert semantic is not None
    assert semantic["metrics"]["linkage_pct"] >= 0


def test_dashboard_html_includes_semantic_section(isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    with TestClient(app) as client:
        r = client.get("/dashboard")
    assert r.status_code == 200
    assert "SEMANTIC MOAT" in r.text or "semantic-layer" in r.text


def test_mcp_has_index_tools():
    import market_mcp

    names = {t["name"] for t in market_mcp.TOOLS}
    assert "index_resolve" in names
    assert "index_lookup" in names
    assert "index_stats" in names
    assert len(market_mcp.TOOLS) >= 46