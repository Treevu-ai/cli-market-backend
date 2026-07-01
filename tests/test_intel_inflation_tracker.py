"""Regression test for cli-market-backend#127: /v1/intel/inflation must read
price_history (append-only) instead of price_snapshots (upserted
one-row-per-product), which can never hold two distinct points in time for
the same product and made this endpoint report 0 products in every country."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from market_core import STORES
from routers import intel as intel_router


@pytest.fixture
def pe_store(monkeypatch):
    stores = {"wong_pe": {"country": "PE", "disabled": False, "line": "supermercados", "currency": "PEN"}}
    monkeypatch.setattr(intel_router, "STORES", stores)
    monkeypatch.setattr(intel_router, "require_api_key", lambda *_a, **_k: "test-user")
    monkeypatch.setattr(intel_router, "_cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(intel_router, "_cache_set", lambda *_a, **_k: None)


def _seed(db):
    # price_snapshots holds only the *current* state (upsert), one row per
    # (product_id, store) — this alone must not drive inflation calculations.
    db.execute(
        """
        INSERT INTO price_snapshots
            (product_id, name, store, store_name, price, list_price, currency, line, queried_at)
        VALUES ('p1', 'Arroz 1kg', 'wong_pe', 'Wong', 5.0, 5.0, 'PEN', 'supermercados', datetime('now'))
        """
    )
    # price_history is append-only: two distinct points in time for the same
    # product, which is what a real price change over the window looks like.
    old = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO price_history (product_id, store, price, list_price, discount, recorded_at) VALUES ('p1', 'wong_pe', 4.0, 4.0, 0, ?)",
        (old,),
    )
    db.execute(
        "INSERT INTO price_history (product_id, store, price, list_price, discount, recorded_at) VALUES ('p1', 'wong_pe', 5.0, 5.0, 0, ?)",
        (now,),
    )
    db.commit()


def test_inflation_tracker_finds_products_from_price_history(pe_store, isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    with TestClient(app):
        # App startup (schema init) happens on entering the context manager.
        db = isolated_db.get_db()
        _seed(db)

        result = intel_router.inflation_tracker(
            country="PE", line=None, days=30, limit=20, authorization="Bearer test", db=db
        )

    assert result["products_tracked"] == 1
    assert result["items"][0]["product_id"] == "p1"
    assert result["items"][0]["delta_pct"] == pytest.approx(25.0)
