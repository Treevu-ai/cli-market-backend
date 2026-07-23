"""Regression tests for mcp_http dispatch bugs found via live agent testing:

- market_discover was wired to /analytics/trending (a market_trending
  copy-paste) instead of composing /lines + /stores + /countries.
- market_price_history was entirely absent from the dispatch table and
  fell through to "Unknown tool", despite /analytics/price-history
  already existing and cli-market-core already expecting it.

2026-07-23 full MCP tools audit added a third bug class on top of these:
_call_tool() never consulted market_mcp_registry's resolve_tool_name(), so
9 of 11 registered legacy aliases (market_alerts, market_stores worked only
because it happened to have its own branch) fell through to "Unknown tool"
instead of redirecting to their replacement. Plus 2 tools whose dispatch
branch still matched a since-renamed tool name (market_forecast ->
market_price_forecast, market_intel_pulse -> market_commerce_pulse), and
16 tools registered with a real, confirmed-existing backend route that
simply never got a dispatch branch at all.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


# ── 2026-07-23 audit: legacy alias resolution ────────────────────────────────


@pytest.mark.parametrize(
    "alias,canonical,url_path",
    [
        ("market_alerts", "market_price_alerts", "/v1/intel/alerts"),
        ("market_notify", "market_price_alerts", "/v1/intel/alerts"),
        ("market_preferences", "market_household_get", "/v1/household"),
        ("market_analytics_indicators", "market_intel_brief", "/v1/intel/brief"),
        ("market_enrichment", "market_intel_brief", "/v1/intel/brief"),
        ("market_enrichment_subcategories", "market_intel_brief", "/v1/intel/brief"),
        ("market_indicators", "market_intel_brief", "/v1/intel/brief"),
        ("market_countries", "market_discover", None),
        ("market_lines", "market_discover", None),
        ("market_reorder", "market_orders", "/orders"),
        ("market_stores", "market_discover", None),
    ],
)
def test_legacy_alias_resolves_to_canonical_tool(alias, canonical, url_path):
    """9 of these 11 aliases had no dispatch branch at all before the fix and
    fell straight to "Unknown tool" — resolve_tool_name() now runs before
    the elif chain so any alias (present or future) redirects automatically."""
    from routers.mcp_http import _call_tool

    if url_path is None:
        # market_discover composes 3 calls itself — just confirm it isn't
        # rejected as unknown; test_market_discover_composes_lines_stores_countries
        # already covers its own behavior in depth.
        responses = {
            _api_url("/lines"): _mock_response(200, {}),
            _api_url("/stores"): _mock_response(200, {}),
            _api_url("/countries"): _mock_response(200, {}),
        }
    else:
        responses = {_api_url(url_path): _mock_response(200, {"ok": True})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool(alias, {}, "sk-test"))

    assert result != {"error": f"Unknown tool: {alias}"}
    assert result != {"error": f"Unknown tool: {canonical}"}


# ── 2026-07-23 audit: renamed tools whose dispatch branch matched the old name ──

def test_market_price_forecast_no_longer_unknown_tool():
    """Dispatch branch still matched the old name "market_forecast"."""
    from routers.mcp_http import _call_tool

    url = _api_url("/v1/intel/forecast")
    responses = {url: _mock_response(200, {"trend": "up"})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_price_forecast", {"product": "leche"}, "sk-test"))

    assert result != {"error": "Unknown tool: market_price_forecast"}
    assert result == {"trend": "up"}


def test_market_commerce_pulse_no_longer_unknown_tool():
    """Dispatch branch still matched the old name "market_intel_pulse"."""
    from routers.mcp_http import _call_tool

    url = _api_url("/v1/intel/pulse")
    responses = {url: _mock_response(200, {"headline": "test"})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_commerce_pulse", {"country": "PE"}, "sk-test"))

    assert result != {"error": "Unknown tool: market_commerce_pulse"}
    assert result == {"headline": "test"}


# ── 2026-07-23 audit: registered tools with a real endpoint but no dispatch ──


def test_market_categories_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/categories/wong_pe")
    responses = {url: _mock_response(200, {"categories": []})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_categories", {"store": "wong_pe"}, "sk-test"))

    assert result != {"error": "Unknown tool: market_categories"}
    assert result == {"categories": []}


def test_market_stock_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/products/stock/p1")
    responses = {url: _mock_response(200, {"stock": 4})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(
            _call_tool("market_stock", {"product_id": "p1", "store": "wong_pe"}, "sk-test")
        )

    assert result != {"error": "Unknown tool: market_stock"}
    assert result == {"stock": 4}


def test_market_exchange_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/v1/utils/exchange")
    responses = {url: _mock_response(200, {"converted": 100.0})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(
            _call_tool(
                "market_exchange",
                {"amount": 10.0, "from_currency": "USD", "to_currency": "PEN"},
                "sk-test",
            )
        )

    assert result != {"error": "Unknown tool: market_exchange"}
    assert result == {"converted": 100.0}


def test_market_voice_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/v1/voice/transcribe-url")
    responses = {url: _mock_response(200, {"text": "leche gloria"})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(
            _call_tool("market_voice", {"url": "https://example.com/a.mp3"}, "sk-test")
        )

    assert result != {"error": "Unknown tool: market_voice"}
    assert result == {"text": "leche gloria"}


def test_index_lookup_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/index/lookup/prod_gloria_lacteos_1l")
    responses = {url: _mock_response(200, {"canonical_name": "Leche Gloria 1L"})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(
            _call_tool("index_lookup", {"product_id": "prod_gloria_lacteos_1l"}, "sk-test")
        )

    assert result != {"error": "Unknown tool: index_lookup"}
    assert result == {"canonical_name": "Leche Gloria 1L"}


def test_index_resolve_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/resolve")
    responses = {url: _mock_response(200, {"canonical_id": "prod_gloria_lacteos_1l"})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("index_resolve", {"name": "Leche Gloria 1L"}, "sk-test"))

    assert result != {"error": "Unknown tool: index_resolve"}
    assert result == {"canonical_id": "prod_gloria_lacteos_1l"}


def test_index_stats_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/index/stats")
    responses = {url: _mock_response(200, {"registry_size": 8000})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("index_stats", {}, "sk-test"))

    assert result != {"error": "Unknown tool: index_stats"}
    assert result == {"registry_size": 8000}


def test_market_basket_stress_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/v1/intel/basket-stress")
    responses = {url: _mock_response(200, {"basket_stress_index": 0.5})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_basket_stress", {"country": "PE"}, "sk-test"))

    assert result != {"error": "Unknown tool: market_basket_stress"}
    assert result == {"basket_stress_index": 0.5}


def test_market_brands_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/analytics/brands")
    responses = {url: _mock_response(200, {"brands": ["gloria"]})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_brands", {"line": "supermercados"}, "sk-test"))

    assert result != {"error": "Unknown tool: market_brands"}
    assert result == {"brands": ["gloria"]}


def test_market_delivery_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/products/delivery/p1")
    responses = {url: _mock_response(200, {"estimated_days": "2-3"})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(
            _call_tool("market_delivery", {"product_id": "p1", "store": "wong_pe"}, "sk-test")
        )

    assert result != {"error": "Unknown tool: market_delivery"}
    assert result == {"estimated_days": "2-3"}


def test_market_ecosystem_traction_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/analytics/observatory")
    responses = {url: _mock_response(200, {"launches": []})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_ecosystem_traction", {"days": 30}, "sk-test"))

    assert result != {"error": "Unknown tool: market_ecosystem_traction"}
    assert result == {"launches": []}


def test_market_enrich_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/products/enrich")
    responses = {url: _mock_response(200, {"matches": []})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_enrich", {"query": "leche"}, "sk-test"))

    assert result != {"error": "Unknown tool: market_enrich"}
    assert result == {"matches": []}


def test_market_enrichment_refresh_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/v1/intel/enrichment/refresh")
    responses = {url: _mock_response(200, {"refreshed": True})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_enrichment_refresh", {"country": "PE"}, "sk-test"))

    assert result != {"error": "Unknown tool: market_enrichment_refresh"}
    assert result == {"refreshed": True}


def test_market_gov_observations_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/v1/intel/gov-observations")
    responses = {url: _mock_response(200, {"observations": []})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_gov_observations", {"region": "PE"}, "sk-test"))

    assert result != {"error": "Unknown tool: market_gov_observations"}
    assert result == {"observations": []}


def test_market_intel_refresh_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/v1/intel/refresh")
    responses = {url: _mock_response(200, {"refreshed": True})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_intel_refresh", {"country": "PE"}, "sk-test"))

    assert result != {"error": "Unknown tool: market_intel_refresh"}
    assert result == {"refreshed": True}


def test_market_scan_no_longer_unknown_tool():
    from routers.mcp_http import _call_tool

    url = _api_url("/v1/admin/scan-stores")
    responses = {url: _mock_response(200, {"scanned": 5})}

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client(responses))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_scan", {"line": "supermercados"}, "sk-test"))

    assert result != {"error": "Unknown tool: market_scan"}
    assert result == {"scanned": 5}
