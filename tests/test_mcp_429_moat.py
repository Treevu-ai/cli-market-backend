"""Tests for Fix C: structured 429 responses and moat freshness injection in mcp_http."""

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


def _mock_response(status_code: int, json_body: dict | None = None, text: str = "", headers: dict | None = None):
    """Build a mock httpx.Response."""
    r = MagicMock()
    r.status_code = status_code
    r.text = text or (str(json_body) if json_body else "")
    r.headers = headers or {}
    r.json = MagicMock(return_value=json_body or {})
    return r


def _mock_client(responses: dict[str, MagicMock]):
    """AsyncMock client whose methods return pre-configured responses."""
    client = AsyncMock()
    client.post = AsyncMock(side_effect=lambda url, **_: responses.get(url, _mock_response(200, {})))
    client.get = AsyncMock(side_effect=lambda url, **_: responses.get(url, _mock_response(200, {})))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


# ── 429 structured response ───────────────────────────────────────────────────

def test_429_with_retry_after_header():
    """_call_tool should return retry_after_seconds (int) when backend returns 429."""
    from routers.mcp_http import _call_tool

    search_url = _api_url("/products/search")
    resp_429 = _mock_response(
        429,
        json_body={"detail": "Rate limit reached (60 req/60s). Retry in 60s. Upgrade at https://cli-market.dev"},
        headers={"retry-after": "60"},
    )

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client({search_url: resp_429}))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_search", {"query": "leche"}, "sk-test"))

    assert result.get("error") == "rate_limited"
    assert "retry_after_seconds" in result
    assert isinstance(result["retry_after_seconds"], int)
    assert result["retry_after_seconds"] == 60


def test_429_message_is_actionable():
    from routers.mcp_http import _call_tool

    search_url = _api_url("/products/search")
    resp_429 = _mock_response(
        429,
        json_body={"detail": "Daily limit reached. Resets in 8h. Upgrade at https://cli-market.dev"},
        headers={"retry-after": "28800"},
    )

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client({search_url: resp_429}))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_search", {"query": "arroz"}, "sk-test"))

    assert result.get("error") == "rate_limited"
    assert "cli-market.dev" in result.get("message", "")


def test_429_without_retry_after_defaults_to_60():
    """If backend omits Retry-After, default to 60."""
    from routers.mcp_http import _call_tool

    search_url = _api_url("/products/search")
    resp_429 = _mock_response(429, json_body={"detail": "Too many requests"}, headers={})

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=_mock_client({search_url: resp_429}))
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(_call_tool("market_search", {"query": "pan"}, "sk-test"))

    assert result.get("retry_after_seconds") == 60


# ── moat freshness injection ──────────────────────────────────────────────────

def _run_search_with_moat(moat_response: dict) -> dict:
    """Run market_search with a mocked collector health response."""
    from routers import mcp_http
    import routers.mcp_http as mcp_module

    search_url = _api_url("/products/search")
    collector_url = _api_url("/health/collector")

    search_result = {"results": [{"name": "Leche Gloria", "price": 4.5}]}
    resp_search = _mock_response(200, json_body=search_result)
    resp_collector = _mock_response(200, json_body=moat_response)

    # Reset cache so the moat endpoint is actually called.
    # Use a large negative ts so now - ts > _MOAT_TTL regardless of system monotonic clock.
    mcp_module._MOAT_CACHE = {"ts": -1e9, "age_hours": None, "status": "unknown"}

    mock_client = _mock_client({search_url: resp_search, collector_url: resp_collector})

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        return asyncio.run(mcp_module._call_tool("market_search", {"query": "leche"}, "sk-test"))


def test_fresh_moat_no_warning():
    result = _run_search_with_moat({"status": "ok", "age_hours": 2.5})
    assert "_data_warning" not in result
    assert result.get("_moat_age_hours") == 2.5


def test_stale_moat_injects_warning():
    result = _run_search_with_moat({"status": "stale", "age_hours": 14.0})
    assert "_data_warning" in result
    assert "14.0h" in result["_data_warning"] or "14" in result["_data_warning"]
    assert result.get("_moat_age_hours") == 14.0


def test_dead_moat_injects_warning():
    result = _run_search_with_moat({"status": "dead", "age_hours": 30.0})
    assert "_data_warning" in result
    assert result.get("_moat_age_hours") == 30.0


def test_no_moat_fields_on_non_freshness_tool():
    """market_trending should not have _moat_age_hours or _data_warning."""
    import routers.mcp_http as mcp_module

    trending_url = _api_url("/analytics/trending")
    resp = _mock_response(200, json_body={"trending": []})

    mcp_module._MOAT_CACHE = {"ts": 0.0, "age_hours": None, "status": "unknown"}
    mock_client = _mock_client({trending_url: resp})

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(mcp_module._call_tool("market_trending", {}, "sk-test"))

    assert "_moat_age_hours" not in result
    assert "_data_warning" not in result


def test_moat_cache_reused_within_ttl():
    """Second call within TTL should not re-fetch collector endpoint."""
    import time
    import routers.mcp_http as mcp_module

    mcp_module._MOAT_CACHE = {"ts": time.monotonic(), "age_hours": 1.0, "status": "ok"}

    search_url = _api_url("/products/search")
    resp_search = _mock_response(200, json_body={"results": []})
    mock_client = _mock_client({search_url: resp_search})

    with patch("routers.mcp_http.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

        asyncio.run(mcp_module._call_tool("market_search", {"query": "pan"}, "sk-test"))

    # collector endpoint should not have been called
    mock_client.get.assert_not_called()
