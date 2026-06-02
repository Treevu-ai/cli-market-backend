"""Tests for per-store credential loading and connector auth headers."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def clean_credentials(monkeypatch):
    import store_credentials

    for key in list(__import__("os").environ):
        if key.startswith("STORE_"):
            monkeypatch.delenv(key, raising=False)
    store_credentials.reload_credentials()
    yield
    store_credentials.reload_credentials()


def test_env_parses_magento_token(monkeypatch):
    import store_credentials

    monkeypatch.setenv("STORE_FALABELLA_PE_MAGENTO_TOKEN", "test-magento-token")
    store_credentials.reload_credentials()

    assert store_credentials.has_store_credentials("falabella_pe")
    cfg = store_credentials.resolve_store_config("falabella_pe")
    assert cfg["magento_token"] == "test-magento-token"
    assert "falabella_pe" in store_credentials.compute_default_stores()


def test_vtex_requires_key_and_token_pair(monkeypatch):
    import store_credentials

    monkeypatch.setenv("STORE_SAMSUNG_MX_VTEX_APP_KEY", "key-only")
    store_credentials.reload_credentials()
    assert not store_credentials.has_store_credentials("samsung_mx")

    monkeypatch.setenv("STORE_SAMSUNG_MX_VTEX_APP_TOKEN", "token-ok")
    store_credentials.reload_credentials()
    assert store_credentials.has_store_credentials("samsung_mx")
    cfg = store_credentials.resolve_store_config("samsung_mx")
    assert cfg["vtex_app_key"] == "key-only"
    assert cfg["vtex_app_token"] == "token-ok"


def test_shopify_storefront_token_enables_store(monkeypatch):
    import store_credentials

    monkeypatch.setenv("STORE_GYMSHARK_STOREFRONT_TOKEN", "shpat_test")
    store_credentials.reload_credentials()

    assert store_credentials.has_store_credentials("gymshark")
    assert "gymshark" in store_credentials.compute_default_stores()
    cfg = store_credentials.resolve_store_config("gymshark")
    assert cfg["storefront_token"] == "shpat_test"


def test_disabled_store_without_credentials_not_in_default():
    import store_credentials

    assert "falabella_pe" not in store_credentials.compute_default_stores()


def test_magento_connector_sends_bearer():
    from market_connectors.magento import MagentoConnector

    async def run():
        connector = MagentoConnector()
        store_config = {
            "base": "https://example.com",
            "magento_token": "secret",
            "currency": "PEN",
            "name": "Test",
        }

        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"items": []}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("market_connectors.magento.httpx.AsyncClient", return_value=mock_client):
            await connector.search(store_config, "leche", limit=5)

        _, kwargs = mock_client.get.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer secret"

    asyncio.run(run())


def test_vtex_connector_sends_app_headers():
    from market_connectors.vtex import VtexConnector

    async def run():
        connector = VtexConnector()
        store_config = {
            "base": "https://example.vtex.com",
            "vtex_app_key": "app-key",
            "vtex_app_token": "app-token",
            "_io_path": "",
        }

        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = []

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("market_connectors.vtex.httpx.AsyncClient", return_value=mock_client):
            await connector.search(store_config, "arroz", limit=5)

        _, kwargs = mock_client.get.call_args
        assert kwargs["headers"]["X-VTEX-API-AppKey"] == "app-key"
        assert kwargs["headers"]["X-VTEX-API-AppToken"] == "app-token"

    asyncio.run(run())
