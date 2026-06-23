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


MOCK_PUBLIC_RESPONSES = {
    "cli-market": {"total_downloads": 5000},
    "cli-market-core": {"total_downloads": 3000},
    "cli-market-world": {"total_downloads": 4500},
}


def _mock_urlopen(req, timeout=None):
    import json
    from io import BytesIO
    from unittest.mock import MagicMock

    url = req.get_full_url() if hasattr(req, "get_full_url") else str(req)
    for name, data in MOCK_PUBLIC_RESPONSES.items():
        if f"/{name}" in url:
            resp = MagicMock()
            resp.read.return_value = json.dumps(data).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp
    raise ValueError(f"Unexpected URL in mock: {url}")


@patch.dict("os.environ", {}, clear=True)
@patch("market_pepy.urllib.request.urlopen", side_effect=_mock_urlopen)
def test_analytics_pypi_public_consolidated(mock_urlopen):
    """Consolidated totals work without PEPY_API_KEY (Pepy v2 public fallback)."""
    import market_pepy as mp

    mp._CACHE.clear()
    mp._CACHE_AT = 0.0
    mp._V2_PUBLIC_CACHE.clear()
    mp._V2_PUBLIC_CACHE_AT = 0.0
    r = client.get("/analytics/pypi")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert int(body.get("total_downloads") or 0) > 0
    assert "consolidated" in (body.get("project") or "").lower()
    breakdown = body.get("breakdown") or {}
    assert breakdown.get("legacy") is not None