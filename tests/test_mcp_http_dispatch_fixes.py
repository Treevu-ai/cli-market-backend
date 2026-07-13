"""Regression tests for two mcp_http dispatch bugs found via live agent testing:

- market_discover was wired to /analytics/trending (a market_trending
  copy-paste) instead of composing /lines + /stores + /countries.
- market_price_history was entirely absent from the dispatch table and
  fell through to "Unknown tool", despite /analytics/price-history
  already existing and cli-market-core already expecting it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _api_url(path: str) -> str:
    from routers.mcp_http import _API_BASE

    return f"{_API_BASE}{path}"


def _mock_response(status_code: int, json_body: dict | None = None, text: str = ""):
    r = MagicMock()
    r.status_code = status_code
    r.text = text or (str(json_body) if json_body else "")
    r.json = MagicMock(return_value=json_body or {})
    return r


def _mock_client(responses: dict[str, MagicMock]):
    """AsyncMock client whose methods return pre-configured responses, keyed by URL."""
    client = AsyncMock()
    client.post = AsyncMock(side_effect=lambda url, **_: responses.get(url, _mock_response(200, {})))
    client.get = AsyncMock(side_effect=lambda url, **_: responses.get(url, _mock_response(200, {})))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def test_market_discover_composes_lines_stores_countries():
    """market_discover must hit /lines + /stores + /countries and merge them —
    not silently return trending data."""
    from routers.mcp_http import _call_tool

    responses = {
        _api_url("/lines"): _mock_response(200, {"lines": ["supermercados", "farmacias"]}),
        _api_url("/stores"): _mock_response(200, {"stores": ["wong_pe", "metro_pe"]}),
        _api_url("/countries"): _mock_response(200, {"countries": ["PE", "AR"]}),
        # If the old bug regresses, this is what would get hit instead.
        _api_url("/analytics/trending"): _mock_response(200, {"trending": ["should not appear"]}),
    }

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_discover", {"country": "PE"}, "sk-test"))

    assert result == {
        "lines": {"lines": ["supermercados", "farmacias"]},
        "stores": {"stores": ["wong_pe", "metro_pe"]},
        "countries": {"countries": ["PE", "AR"]},
    }
    assert "trending" not in result


def test_market_discover_propagates_upstream_error():
    from routers.mcp_http import _call_tool

    responses = {
        _api_url("/lines"): _mock_response(200, {"lines": []}),
        _api_url("/stores"): _mock_response(500, text="db down"),
        _api_url("/countries"): _mock_response(200, {"countries": []}),
    }

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_discover", {}, "sk-test"))

    assert result.get("error") == "HTTP 500"


def test_market_price_history_no_longer_unknown_tool():
    """market_price_history must route to /analytics/price-history —
    previously absent from the dispatch table entirely."""
    from routers.mcp_http import _call_tool

    history_url = _api_url("/analytics/price-history")
    responses = {
        history_url: _mock_response(200, {"snapshots": [{"price": 5.2, "store": "wong_pe"}]}),
    }

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(
            _call_tool("market_price_history", {"product_id": "p1", "store": "wong_pe"}, "sk-test")
        )

    assert result != {"error": "Unknown tool: market_price_history"}
    assert result == {"snapshots": [{"price": 5.2, "store": "wong_pe"}]}


def test_market_price_risk_no_longer_hits_alerts_endpoint():
    """market_price_risk was routed to /v1/intel/alerts (market_price_alerts'
    endpoint, which requires a mandatory `product` param market_price_risk's
    own schema doesn't have) -> every call 422'd. Must hit /v1/intel/price-risk."""
    from routers.mcp_http import _call_tool

    price_risk_url = _api_url("/v1/intel/price-risk")
    alerts_url = _api_url("/v1/intel/alerts")
    responses = {
        price_risk_url: _mock_response(200, {"risk_level": "moderate"}),
        alerts_url: _mock_response(422, text="field required: product"),
    }

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_price_risk", {"country": "PE"}, "sk-test"))

    assert result == {"risk_level": "moderate"}


def test_market_informal_signal_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/v1/intel/informal-signal")
    responses = {url: _mock_response(200, {"confidence": "low"})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_informal_signal", {"country": "PE"}, "sk-test"))

    assert result != {"error": "Unknown tool: market_informal_signal"}
    assert result == {"confidence": "low"}


def test_market_promo_detector_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/v1/intel/promo-detector")
    responses = {url: _mock_response(200, {"authentic": True})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(
            _call_tool("market_promo_detector", {"product": "aceite"}, "sk-test")
        )

    assert result != {"error": "Unknown tool: market_promo_detector"}
    assert result == {"authentic": True}


def test_market_retailer_scorecard_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/v1/intel/retailer-scorecard")
    responses = {url: _mock_response(200, {"score": 82})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(
            _call_tool("market_retailer_scorecard", {"store": "wong_pe"}, "sk-test")
        )

    assert result != {"error": "Unknown tool: market_retailer_scorecard"}
    assert result == {"score": 82}
