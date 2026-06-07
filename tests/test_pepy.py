"""Pepy.tech PyPI stats integration."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from market_server import app

client = TestClient(app)

MOCK_PEPY = {
    "id": "cli-market-world",
    "total_downloads": 11155,
    "versions": ["1.9.4", "1.6.0"],
    "downloads": {
        "2026-06-01": {"1.9.4": 100, "1.6.0": 50},
        "2026-06-06": {"1.9.4": 200},
        "2026-06-07": {"1.9.4": 40},
    },
    "metadata": {"latest_version": "1.9.4"},
}


@patch.dict("os.environ", {"PEPY_API_KEY": "test-key"}, clear=False)
@patch("market_pepy._fetch_json")
def test_pepy_summary(mock_fetch):
    from market_pepy import pepy_summary

    def side_effect(path: str):
        if "service-api" in path:
            return {"downloads": {"2026-06-06": {"1.9.4": 150}}}
        return MOCK_PEPY

    mock_fetch.side_effect = side_effect
    import market_pepy as mp

    mp._CACHE.clear()
    mp._CACHE_AT = 0.0
    data = pepy_summary(force=True)
    assert data["ok"] is True
    assert data["total_downloads"] == 11155
    assert data["latest_version"] == "1.9.4"
    assert data["downloads_last_30d"] >= 390


@patch.dict("os.environ", {}, clear=True)
def test_analytics_pypi_unconfigured():
    import market_pepy as mp

    mp._CACHE.clear()
    mp._CACHE_AT = 0.0
    r = client.get("/analytics/pypi")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert body.get("configured") is False