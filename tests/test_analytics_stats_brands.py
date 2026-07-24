"""unique_brands_on_shelf on GET /analytics/stats.

Counts distinct brands with a priced snapshot inside a recent window
(STATS_BRAND_FRESHNESS_DAYS, default 30d) — brands that are actually for
sale right now, not every brand ever seen historically.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _product(product_id, brand, price=4.5, store="wong"):
    return {
        "id": product_id,
        "product_id": product_id,
        "name": f"Producto {product_id}",
        "brand": brand,
        "price": price,
        "store": store,
        "store_name": store.capitalize(),
        "currency": "PEN",
        "line": "supermercados",
        "line_name": "Supermercados",
        "category": "lacteos",
        "stock": 10,
        "url": f"http://example.com/{product_id}",
    }


def _setup_user(market_core, username="stats-user"):
    from market_core import db_create_api_key, db_set_subscription

    market_core.ensure_db_initialized()
    db_set_subscription(username, "pro")
    key_rec = db_create_api_key(username, scopes="read", label="e2e")
    return key_rec["key"]


@pytest.fixture
def stats_client(isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    market_core = isolated_db
    market_core.ensure_db_initialized()
    return TestClient(app), market_core


def test_unique_brands_on_shelf_counts_only_fresh_priced_snapshots(stats_client):
    client, mc = stats_client
    api_key = _setup_user(mc)

    mc.save_price_snapshot(_product("p1", "Gloria"))
    mc.save_price_snapshot(_product("p2", "Laive"))
    # Same brand, different product — must not double count.
    mc.save_price_snapshot(_product("p3", "Gloria", store="metro"))

    # Stale snapshot (outside the freshness window) — must be excluded.
    mc.save_price_snapshot(_product("p4", "Ideal"))
    stale_at = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    db = mc.get_db()
    db.execute(
        "UPDATE price_snapshots SET queried_at = ? WHERE product_id = 'p4'",
        (stale_at,),
    )
    db.commit()
    db.close()

    # Zero-priced snapshot — must be excluded (not actually on sale).
    mc.save_price_snapshot(_product("p5", "Bonlé", price=0))

    r = client.get("/analytics/stats", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unique_brands_on_shelf"] == 2
    assert body["brands_on_shelf_window_days"] == 30


def test_unique_brands_on_shelf_collapses_case_variants(stats_client):
    """Regression for the 2026-07-24 finding: "Gloria" vs "GLORIA" (same
    brand, different retailer casing) must count once, not twice — this hit
    ~43% of all branded snapshots in production (1,520 brand groups)."""
    client, mc = stats_client
    api_key = _setup_user(mc)

    mc.save_price_snapshot(_product("p1", "Gloria"))
    mc.save_price_snapshot(_product("p2", "GLORIA", store="metro"))
    mc.save_price_snapshot(_product("p3", "gloria ", store="plazavea"))

    r = client.get("/analytics/stats", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 200, r.text
    assert r.json()["unique_brands_on_shelf"] == 1


def test_analytics_brands_collapses_case_variants(stats_client):
    """Regression for the same finding on GET /analytics/brands — must
    report one consolidated count per brand, using the most common casing
    as the display form, not one row per verbatim spelling."""
    client, mc = stats_client
    api_key = _setup_user(mc)

    mc.save_price_snapshot(_product("p1", "Gloria"))
    mc.save_price_snapshot(_product("p2", "GLORIA", store="metro"))
    mc.save_price_snapshot(_product("p3", "Gloria", store="plazavea"))
    mc.save_price_snapshot(_product("p4", "Laive", store="tottus"))

    r = client.get("/analytics/brands", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 200, r.text
    brands = {row["brand"]: row["count"] for row in r.json()["brands"]}
    assert brands == {"Gloria": 3, "Laive": 1}
