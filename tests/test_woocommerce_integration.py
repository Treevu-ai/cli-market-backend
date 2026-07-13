"""WooCommerce connector integration smoke tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_xray_pe_store_api_search():
    """Live smoke test against xray.pe's public WooCommerce Store API — only
    meaningful when that retailer is approved in the local/CI store DB.

    Regression: this used to hardcode a sys.path fallback to a stale local
    checkout (`Projects/cli-market-core`, predating cli-market-core being a
    proper pip package) as a way to find xray_pe pre-seeded there. Whether
    the test passed or failed then depended on *import order* — only the
    first test file in the whole suite to import market_core would trigger
    that fallback path and shadow the real installed package; any other
    file importing market_core first left it cached in sys.modules, and
    the real package has no xray_pe configured locally. Skip cleanly
    instead of depending on that machine-specific side effect.
    """
    from market_connectors.woocommerce import WooCommerceConnector
    from market_core.store_credentials import get_store_profile

    cfg = get_store_profile("xray_pe")
    if not cfg:
        pytest.skip("xray_pe not configured in this environment's store DB")

    connector = WooCommerceConnector()
    items = await connector.search(cfg, "ecu", limit=5)
    assert items, "expected products from public Store API"
    prod = connector.normalize(items[0], "xray_pe", cfg)
    assert prod["price"] > 0
    assert prod["currency"] == "PEN"
    assert prod["url"]