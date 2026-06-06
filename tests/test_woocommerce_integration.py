"""WooCommerce connector integration smoke tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT.parent / "Projects" / "cli-market-core"
for p in (REPO_ROOT, CORE_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.mark.asyncio
async def test_xray_pe_store_api_search():
    from market_connectors.woocommerce import WooCommerceConnector
    from market_core.store_credentials import resolve_store_config

    cfg = resolve_store_config("xray_pe")
    connector = WooCommerceConnector()
    items = await connector.search(cfg, "ecu", limit=5)
    assert items, "expected products from public Store API"
    prod = connector.normalize(items[0], "xray_pe", cfg)
    assert prod["price"] > 0
    assert prod["currency"] == "PEN"
    assert prod["url"]