"""GET /stores must not 500 when catalog includes all configured lines."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_list_stores_includes_woocommerce_pilots():
    import market_server
    from market_core import STORES

    client = TestClient(market_server.app)
    r = client.get("/stores")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == len(STORES)
    assert "xray_pe" in body["stores"]
    assert "nunaorganica_pe" in body["stores"]
    assert body["stores"]["xray_pe"]["line_name"] == "Automotriz"